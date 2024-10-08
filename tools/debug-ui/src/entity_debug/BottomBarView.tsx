import { useContext } from 'react';
import { ColdStorageChoiceContext, PinnedKeysContext } from './pinned_keys';
import './BottomBarView.scss';
import { ShowAsciiCharactersInHexContext } from './view_options';

export const BottomBarView = () => {
    const { keys, dispatch } = useContext(PinnedKeysContext);
    const { coldStorage, dispatch: setColdStorage } = useContext(ColdStorageChoiceContext);
    const { showAscii, dispatch: setShowAscii } = useContext(ShowAsciiCharactersInHexContext);
    return (
        <div className="bottom-bar">
            <div className="cold-storage-choice">
                <input
                    id="cold-storage-checkbox"
                    type="checkbox"
                    checked={coldStorage}
                    onChange={(e) => setColdStorage(e.target.checked)}
                />
                <label htmlFor="cold-storage-checkbox">Use cold storage</label>
            </div>
            <div className="pinned-keys-title">Pinned keys:</div>
            {keys.map((key) => (
                <div key={key.type()} className="pinned-key">
                    <div className="pinned-key-type">{key.type()}</div>
                    <div className="pinned-key-value">{key.toString()}</div>
                    <div
                        className="pinned-key-delete"
                        onClick={() => dispatch({ type: 'remove-key', keyType: key.type() })}>
                        ×
                    </div>
                </div>
            ))}
            {keys.length == 0 && (
                <div className="pinned-keys-empty">(Click an entity key to pin it)</div>
            )}
            <div className="spacer"></div>
            <div className="show-ascii">
                <input
                    id="show-ascii-checkbox"
                    type="checkbox"
                    checked={showAscii}
                    onChange={(e) => setShowAscii(e.target.checked)}
                />
                <label htmlFor="show-ascii-checkbox">Show ASCII in hex</label>
            </div>
        </div>
    );
};
