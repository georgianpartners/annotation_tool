import os
from pathlib import Path
from typing import Dict, List

from alchemy.inference.base import ITextCatModel
from alchemy.shared.config import Config
from alchemy.shared.file_adapters import file_exists, load_jsonl, save_jsonl


def _predict(data_fname, model, data_store) -> List[Dict]:
    df = load_jsonl(data_fname, data_store=data_store)
    results = model.predict(df["text"])
    # Attaching the meta data for an entity (e.g., name and domain)
    for i, res in enumerate(results):
        res.update({"meta": df["meta"][i]})
    return results


def get_predicted(data_fname, model: ITextCatModel, data_store, cache=True):

    # TODO cache key is not unique, and we need a way to expire the cache.
    #  Also, it's unclear if caching helps, unless we move to a real-time
    #  active learning strategy.
    #  For now, we'll turn off cache.

    # TODO since we always turn off the cache, we can safely assume we run
    #  the prediction on every call, which means we can change the format of
    #  the result without worrying breaking the code of loading cached result.

    cache = False

    print(f"get_predicted model={model} data_fname={data_fname} (cache={cache})")

    if not cache:
        return _predict(data_fname, model, data_store=data_store)
    else:
        # Get cache filename
        stem = Path(data_fname).stem
        fname = f"{stem}__inferred_by__{model}.jsonl"
        path = [Config.get_inference_cache_dir(), fname]
        fname = os.path.join(*path)

        if not file_exists(fname, data_store=data_store):
            res = _predict(data_fname, model, data_store=data_store)

            # Save results to cache
            save_jsonl(fname, res, data_store=data_store)

        print(f"Reading from cache: {fname}")
        return load_jsonl(fname, to_df=False, data_store=data_store)
