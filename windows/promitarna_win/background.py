"""Plnění seznamu filmů a seriálů na pozadí (Windows): totéž, co na televizi dělá služba ``IndexService``.

Procházení samo spouští logika (``quick_sync`` po otevření okna). Když se tehdy spustit nemohlo (chybělo
přihlášení nebo klíč TMDB) nebo se zastavilo (výpadek spojení), tady se každých pár minut znovu
rozběhne (``index_keepalive``) – a hned po změně nastavení, profilu nebo obnově zálohy.

Zatímco procházení běží, počítač neusne (jen samotný počítač, obrazovka zhasnout smí), nejvýš
``MAX_HOURS`` od spuštění programu – stejně jako televize v noci.
"""

import sys
import threading
import time

CHECK_EVERY = 5 * 60     # s mezi kontrolami
FIRST_CHECK = 60         # s po spuštění (první spuštění obstará quick_sync z okna)
MAX_HOURS = 10           # tak dlouho nejvýš drží počítač vzhůru

# akce, po kterých se procházení zkusí spustit hned (nové přihlášení, klíč TMDB, jiný profil, záloha)
KICK_PREFIXES = ("settings_set", "profile", "backup")

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _stay_awake(on):
    """Windows: nenechat počítač usnout (``on``), nebo to zase povolit. Jinde nic nedělá."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:
        return False


class Keeper(object):
    def __init__(self, call, check_every=CHECK_EVERY, first_check=FIRST_CHECK, max_hours=MAX_HOURS):
        self.call = call
        self.check_every = check_every
        self.first_check = first_check
        self.deadline = time.time() + max_hours * 3600
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.active = False
        self.awake = False
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name="seznam-na-pozadi")
        self.thread.daemon = True
        self.thread.start()

    def kick(self, action=""):
        """Po akci z okna: když mohla procházení umožnit, zkusí se spustit hned."""
        if not action or action.startswith(KICK_PREFIXES):
            self.wake.set()

    def stop(self):
        self.stopped.set()
        self.wake.set()            # vlákno skončí a samo povolí spánek (stav patří jemu)

    def check(self):
        answer = self.call("index_keepalive", {}) or {}
        self.active = bool(answer.get("active"))
        want = self.active and time.time() < self.deadline
        if want != self.awake:
            # vlákno drží stav jen pro sebe: zapnout i vypnout se musí z téhož vlákna
            _stay_awake(want)
            self.awake = want
        return self.active

    def _run(self):
        delay = self.first_check
        while not self.stopped.is_set():
            self.wake.wait(delay)
            if self.stopped.is_set():
                break
            self.wake.clear()
            try:
                self.check()
            except Exception:   # vlákno na pozadí nesmí shodit program
                pass
            delay = self.check_every
        if self.awake:
            _stay_awake(False)
            self.awake = False
