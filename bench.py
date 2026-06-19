import sys, time, tracemalloc
from book_splitter.adapters.registry import get_adapter
from book_splitter.detector import detect

def bench(path, out="bench_out"):
    tracemalloc.start()
    t0 = time.perf_counter()
    adapter = get_adapter(path)
    plan = detect(adapter.load(path), level_filter="auto")
    adapter.make_writer().write(plan, out)
    dt = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"{path}: {len(plan.chapters)} ch  peak~{peak/1e6:.1f} MB  {dt*1000:.0f} ms")

if __name__ == "__main__":
    for p in sys.argv[1:]:
        bench(p)