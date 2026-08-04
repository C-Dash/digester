"""
Background worker

Runs one Digester operation on a QThread so the GUI stays responsive, and
redirects the digester's log callback to a Qt signal that Qt routes safely back
to the main thread.
"""

from PySide6.QtCore import QThread, Signal

from ..digester import Digester


class Worker(QThread):
    """Runs one digester operation off the main thread.

    The operation is a bound callable plus its arguments, so there is no op-name
    string to typo and no dispatch table to keep in sync. A multi-step operation
    should be a single callable that performs every step (see
    ``MainWindow._open_and_scan``) rather than two chained ``_run`` calls: the
    ``done`` signal fires from inside ``run()``, so a second ``_run`` invoked
    from an ``on_finish`` handler can still see ``isRunning()`` and be refused.

    Threading model
    ---------------
    The Digester holds a single SQLite connection shared between this worker
    thread and the GUI main thread (the connection is opened with
    ``check_same_thread=False``).  Safety depends on that connection never
    being touched concurrently from both threads.  This is enforced by the
    main window, not by a lock in the persistence layer:

      * Long-running operations run here, on the worker thread.
      * Main-thread DB reads (folder clicks) run only when the window is idle.
      * ``MainWindow._run`` gates the UI busy (``_set_busy``) for the lifetime
        of a worker, so the main thread cannot issue a query or start another
        operation until ``done`` fires.  The two therefore never overlap.
    """

    log_message = Signal(str, str)   # (message, level)
    done        = Signal()           # avoid shadowing QThread.finished
    error       = Signal(str)

    def __init__(self, digester: Digester, fn, *args, **kwargs):
        super().__init__()
        self.digester = digester
        self._fn      = fn
        self._args    = args
        self._kwargs  = kwargs

    def run(self):
        # Redirect digester log to a cross-thread signal. Anything the callable
        # logs via digester.log — including nested service calls — reaches the
        # console this way, which is why worker-side work should log rather
        # than return values.
        original_log = self.digester.log
        self.digester.log = lambda msg, lvl: self.log_message.emit(msg, lvl)
        try:
            self._fn(*self._args, **self._kwargs)
        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.digester.log = original_log
            self.done.emit()
