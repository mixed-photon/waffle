from __future__ import annotations

import argparse
import json
from pathlib import Path

from waffle_pdk_demo import mmi


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("mmi.gds"))
    args = parser.parse_args()
    component = mmi()
    sidecar = component.export(args.output)
    _, binding = component.bind()
    print(
        json.dumps(
            {
                "gds": str(args.output.resolve()),
                "sidecar": str(sidecar.resolve()),
                "model": str(component.mapping.model),
                "binding": binding.model_dump(mode="json"),
                "response": {
                    str(k): [v.real, v.imag] for k, v in component.evaluate().items()
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
