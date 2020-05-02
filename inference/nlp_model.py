import hashlib
import numpy as np

from inference.base import ITextCatModel
from db.model import Model


class NLPModel(ITextCatModel):
    # TODO exploit vs explore
    def __init__(self, dbsession, model_id):
        self.dbsession = dbsession
        self.model_id = model_id
        self._cache = None

    def __str__(self):
        # Note: This is also used as a cache key.
        return f"NLPModel-model_id={self.model_id}"

    def _warm_up_cache(self):
        if self._cache is None:
            print("Warming up NLPModel cache...")

            self._cache = {}

            model = self.dbsession.query(Model).filter_by(
                id=self.model_id).one_or_none()

            for fname in model.get_inference_fnames():
                df = model.export_inference(fname, include_text=True)
                df['hashed_text'] = df['text'].apply(_hash_text)
                self._cache.update(
                    dict(zip(df['hashed_text'], df['probs'])))

        return self._cache

    def predict(self, text_list):
        self._warm_up_cache()

        res = []
        for text in text_list:
            prob = self._cache.get(_hash_text(text))
            if prob is None:
                # TODO run any inferences that have not been ran, instead of silently erroring out
                res.append({'score': 0., 'prob': None})
            else:
                res.append({'score': _get_uncertainty(prob), 'prob': prob})

        return res


def _hash_text(text):
    text = text or ''
    return hashlib.md5(text.strip().encode()).hexdigest()


def _get_uncertainty(pred, eps=1e-6):
    # Return entropy as the uncertainty value
    pred = np.array(pred)
    return float(-np.sum(pred * np.log(pred + eps)))
