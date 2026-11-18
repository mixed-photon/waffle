"""Running gdsfactory without leaking global state.

gdsfactory keeps three pieces of process-wide state that matter here (verified for 9.51.0):

* the active PDK (`gdsfactory.pdk._ACTIVE_PDK`), which `Component.add_polygon` and
  `add_port` consult even for plain integer layer indexes. `Pdk.activate()` is not usable: it
  rewrites the layers and database unit of the global default layout `kfactory.kcl` and
  refuses to change the unit once that layout holds cells;
* the default layout `kfactory.kcl`, where `@gf.cell` functions and `gf.Component()` put every
  cell they build, with a cache keyed by arguments; and
* the registry of layouts (`kfactory.layout.kcls`) and of klayout libraries.

`session` therefore builds on a private layout with the technology's database unit, swaps in
a minimal throwaway PDK for the duration (the only place a private module attribute is
touched), and on exit restores the previous PDK and deletes the layout and its registry
entries. Nothing built inside a session is usable after it: callers convert to Layout IR
first. Sessions are serialized by a lock because the active PDK is process-wide; other code
that activates a PDK concurrently in another thread is not protected.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import gdsfactory as gf
import gdsfactory.pdk as gf_pdk
import kfactory as kf

_LOCK = threading.RLock()
_COUNTER = itertools.count(1)


@contextmanager
def session(dbu_pm: int) -> Iterator[kf.KCLayout]:
    """A private `KCLayout` whose database unit is `dbu_pm` picometres."""
    with _LOCK:
        name = f"waffle_gdsfactory_{next(_COUNTER)}"
        kcl = kf.KCLayout(name)
        kcl.dbu = dbu_pm / 1e6
        previous = gf_pdk._ACTIVE_PDK
        gf_pdk._ACTIVE_PDK = gf.Pdk(name=name)
        try:
            yield kcl
        finally:
            gf_pdk._ACTIVE_PDK = previous
            if kcl.library is not None:
                kcl.library.delete()
            kf.layout.kcls.pop(name, None)
