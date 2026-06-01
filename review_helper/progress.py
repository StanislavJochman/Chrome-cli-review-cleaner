"""Progress reporting for CLI operations."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def _use_progress() -> bool:
    return sys.stderr.isatty()


def default_workers(item_count: int) -> int:
    return max(1, item_count)


def iterate_with_progress(
    items: Iterable[T],
    *,
    desc: str,
    unit: str = "it",
    label: Callable[[T], str] | None = None,
) -> Iterable[T]:
    if not _use_progress():
        yield from items
        return

    from tqdm import tqdm

    bar = tqdm(items, desc=desc, unit=unit, file=sys.stderr, dynamic_ncols=True)
    for item in bar:
        if label is not None:
            bar.set_postfix_str(label(item), refresh=False)
        yield item


def map_with_progress(
    func: Callable[[T], R],
    items: list[T],
    *,
    desc: str,
    unit: str = "it",
    label: Callable[[T], str] | None = None,
    parallel: bool = True,
) -> list[R]:
    if not items:
        return []

    workers = 1 if not parallel else default_workers(len(items))
    if workers == 1:
        if _use_progress():
            results: list[R] = []
            for item in iterate_with_progress(
                items, desc=desc, unit=unit, label=label
            ):
                results.append(func(item))
            return results
        return [func(item) for item in items]

    results: list[R | None] = [None] * len(items)
    indexed = list(enumerate(items))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(func, item): idx for idx, item in indexed}
        if _use_progress():
            from tqdm import tqdm

            bar = tqdm(
                total=len(items),
                desc=desc,
                unit=unit,
                file=sys.stderr,
                dynamic_ncols=True,
            )
            try:
                for future in as_completed(futures):
                    idx = futures[future]
                    results[idx] = future.result()
                    if label is not None:
                        bar.set_postfix_str(label(items[idx]), refresh=False)
                    bar.update(1)
            finally:
                bar.close()
        else:
            for future in as_completed(futures):
                idx = futures[future]
                results[idx] = future.result()

    return [r for r in results if r is not None]


def status(message: str) -> None:
    if _use_progress():
        from tqdm import tqdm

        tqdm.write(message, file=sys.stderr)
    else:
        print(message, file=sys.stderr)
