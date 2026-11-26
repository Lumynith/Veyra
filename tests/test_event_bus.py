import threading


def test_worker_publish_is_delivered_on_gui_thread(qapp, bus):
    got = []
    bus.subscribe("x", lambda p: got.append((p, threading.current_thread() is threading.main_thread())))
    t = threading.Thread(target=bus.publish, args=("x", 42))
    t.start(); t.join()
    assert got == []  # queued, not run on the worker
    qapp.processEvents()
    assert got == [(42, True)]


def test_failing_subscriber_does_not_block_others(qapp, bus):
    got = []
    bus.subscribe("x", lambda p: 1 / 0)
    bus.subscribe("x", got.append)
    bus.publish("x", 1)
    assert got == [1]
