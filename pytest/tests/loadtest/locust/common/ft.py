import logging
from concurrent import futures
import random
import string
import sys
import pathlib
import typing
from locust import events

sys.path.append(str(pathlib.Path(__file__).resolve().parents[4] / 'lib'))

import key
from account import TGAS
from common.base import Account, Deploy, NearNodeProxy, NearUser, FunctionCall, INIT_DONE


class FTContract:
    # NEAR balance given to contracts, doesn't have to be much since users are
    # going to pay for storage
    INIT_BALANCE = NearUser.INIT_BALANCE

    def __init__(self, account: Account, ft_distributor: Account, code: str):
        self.account = account
        self.ft_distributor = ft_distributor
        self.registered_users = []
        self.code = code

    def install(self, node: NearNodeProxy, parent: Account):
        """
        Deploy and initialize the contract on chain.
        The account is created if it doesn't exist yet.
        """
        existed = node.prepare_account(self.account, parent,
                                       FTContract.INIT_BALANCE,
                                       "create contract account")
        if not existed:
            node.send_tx_retry(Deploy(self.account, self.code, "FT"),
                               "deploy ft")
            self.init_contract(node)

    def init_contract(self, node: NearNodeProxy):
        node.send_tx_retry(InitFT(self.account), "init ft")

    def register_user(self, user: NearUser):
        user.send_tx_retry(InitFTAccount(self.account, user.account),
                           locust_name="Init FT Account")
        user.send_tx_retry(TransferFT(self.account,
                                      self.ft_distributor,
                                      user.account_id,
                                      how_much=10**8),
                           locust_name="FT Funding")
        self.registered_users.append(user.account_id)

    def register_passive_user(self, node: NearNodeProxy, account: Account):
        """
        Passive users are only used as receiver, not as signer.
        """
        node.send_tx_async(InitFTAccount(self.account, account),
                           locust_name="Init FT Account")
        self.registered_users.append(account.key.account_id)

    def random_receiver(self, sender: str) -> str:
        return self.random_receivers(sender, 1)[0]

    def random_receivers(self, sender: str, num) -> typing.List[str]:
        rng = random.Random()
        receivers = rng.sample(self.registered_users, num)
        # Sender must be != receiver but maybe there is no other registered user
        # yet, so we just send to the ft_distributor account which is registered
        # from the start
        return list(
            map(lambda a: a.replace(sender, self.ft_distributor.key.account_id),
                receivers))

    def create_passive_users(self,
                             num: int,
                             node: NearNodeProxy,
                             parent: Account,
                             max_account_id_len=64):
        """
        Create on-chain accounts and register them as FT users.
        Note that these are not locust users and they are not able to sign
        transactions. They are only used as targets of transactions and as a
        side-effect they also increase the state size of the contract.
        """
        prefix_len = max_account_id_len - len(parent.key.account_id) - 1
        assert prefix_len > 4, f"user key {parent.key.account_id} is too long"
        chars = string.ascii_lowercase + string.digits

        def create_account_id(i):
            prefix = ''.join(random.Random(i).choices(chars, k=prefix_len))
            account_id = f"{prefix}.{parent.key.account_id}"
            return account_id

        with futures.ThreadPoolExecutor(max_workers=4) as executor:
            batch_size = 10000
            num_batches = (num + batch_size - 1) // batch_size
            # If the last account exists, we assume that all accounts exist.
            if node.account_exists(create_account_id(num - 1)):
                logging.info(
                    f"{parent.key.account_id}: Skipping creation of passive users, already present."
                )
                create_accounts = False
            else:
                create_accounts = True

            for i in range(num_batches):
                account_ids = [
                    create_account_id(i)
                    for i in range(i * batch_size, min((i + 1) *
                                                       batch_size, num))
                ]
                if create_accounts:
                    accounts = [
                        Account(key.Key.from_seed_testonly(account_id))
                        for account_id in account_ids
                    ]
                    node.prepare_accounts(accounts,
                                          parent,
                                          balance=1,
                                          msg="create passive user")
                    futures.wait(
                        executor.submit(self.register_passive_user, node,
                                        account) for account in accounts)
                else:
                    self.registered_users.extend(account_ids)

                logging.info(
                    f"{parent.key.account_id}: Processed batch {i + 1}/{num_batches}, created {(i + 1) * batch_size} users"
                )


class TransferFT(FunctionCall):

    def __init__(self,
                 ft: Account,
                 sender: Account,
                 recipient_id: str,
                 how_much=1):
        # Attach exactly 1 yoctoNEAR according to NEP-141 to avoid calls from restricted access keys
        super().__init__(sender, ft.key.account_id, "ft_transfer", balance=1)
        self.ft = ft
        self.sender = sender
        self.recipient_id = recipient_id
        self.how_much = how_much

    def args(self) -> dict:
        return {
            "receiver_id": self.recipient_id,
            "amount": str(int(self.how_much)),
        }

    def attached_gas(self) -> int:
        """
        We overwrite this setting to minimize effects on congestion control that relies on attached
        gas to determine the capacity of delayed receipt queues. See
        https://near.zulipchat.com/#narrow/stream/295306-contract-runtime/topic/ft_transfer.20benchmark/near/448814523
        for more details.
        """
        return 10 * TGAS

    def sender_account(self) -> Account:
        return self.sender


class InitFT(FunctionCall):

    def __init__(self, contract: Account):
        super().__init__(contract, contract.key.account_id, "new_default_meta")
        self.contract = contract

    def args(self) -> dict:
        return {
            "owner_id": self.contract.key.account_id,
            "total_supply": str(10**33)
        }

    def attached_gas(self) -> int:
        """
        Avoid attaching excess gas to prevent triggering false-positive congestion control.
        """
        return 10 * TGAS

    def sender_account(self) -> Account:
        return self.contract


class InitFTAccount(FunctionCall):

    def __init__(self, contract: Account, account: Account):
        super().__init__(account,
                         contract.key.account_id,
                         "storage_deposit",
                         balance=int(1E23))
        self.contract = contract
        self.account = account

    def args(self) -> dict:
        return {"account_id": self.account.key.account_id}

    def attached_gas(self) -> int:
        """
        Avoid attaching excess gas to prevent triggering false-positive congestion control.
        """
        return 10 * TGAS

    def sender_account(self) -> Account:
        return self.account


@events.init.add_listener
def on_locust_init(environment, **kwargs):
    INIT_DONE.wait()
    node = NearNodeProxy(environment)
    ft_contract_code = environment.parsed_options.fungible_token_wasm
    num_ft_contracts = environment.parsed_options.num_ft_contracts
    funding_account = NearUser.funding_account
    parent_id = funding_account.key.account_id
    run_id = environment.parsed_options.run_id

    funding_account.refresh_nonce(node.node)

    environment.ft_contracts = []
    # TODO: Create accounts in parallel
    for i in range(num_ft_contracts):
        if environment.parsed_options.fixed_contract_names:
            account_id = f"ft{run_id}_{i}.{parent_id}"
            contract_key = key.Key.from_seed_testonly(account_id)
        else:
            account_id = environment.account_generator.random_account_id(
                parent_id, '_ft')
            contract_key = key.Key.from_random(account_id)
        ft_account = Account(contract_key)
        ft_contract = FTContract(ft_account, ft_account, ft_contract_code)
        ft_contract.install(node, funding_account)
        if environment.parsed_options.num_passive_users > 0:
            ft_contract.create_passive_users(
                environment.parsed_options.num_passive_users, node,
                funding_account)
        environment.ft_contracts.append(ft_contract)
        logging.info(f"Finished setup for account {i} on worker {parent_id}")


# FT specific CLI args
@events.init_command_line_parser.add_listener
def _(parser):
    parser.add_argument("--fungible-token-wasm",
                        default="res/fungible_token.wasm",
                        help="Path to the compiled Fungible Token contract")
    parser.add_argument(
        "--num-ft-contracts",
        type=int,
        default=4,
        help=
        "How many different FT contracts to spawn from this worker (FT contracts are never shared between workers)"
    )
    parser.add_argument(
        "--fixed-contract-names",
        action='store_true',
        help=
        "Whether the names of FT contracts will deterministically based on worker id and run id."
    )
    parser.add_argument(
        "--num-passive-users",
        type=int,
        default=0,
        help="Number of passive users to create in each FT contract.")
