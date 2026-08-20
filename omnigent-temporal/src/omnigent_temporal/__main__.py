"""Small CLI: submit a prompt, read state, interrupt.

    python -m omnigent_temporal submit <key> "<text>"
    python -m omnigent_temporal state <key>
    python -m omnigent_temporal interrupt <key>
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys

from .client import interrupt, session_state, submit_prompt


async def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    command, key = argv[0], argv[1]
    if command == "submit":
        prompt_id = await submit_prompt(key, argv[2])
        print(json.dumps({"promptId": prompt_id}))
    elif command == "state":
        print(json.dumps(dataclasses.asdict(await session_state(key)), indent=2))
    elif command == "interrupt":
        await interrupt(key)
        print(json.dumps({"interrupted": key}))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
