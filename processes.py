import time
from multiprocessing import Process, Queue


def process_loop(pid: str, max: int, q: Queue):
    i = 1
    while True:
        q.put(f"${pid}: ${i}")
        i += 1
        if i >= max:
            return
        time.sleep(1)


if __name__ == "__main__":
    q = Queue()
    [p1, p2] = [
        Process(target=process_loop, args=("a", 2, q)),
        Process(target=process_loop, args=("b", 3, q)),
    ]
    p1.start()
    p2.start()
    while True:
        print(q.get())
    p1.join()
    p2.join()
