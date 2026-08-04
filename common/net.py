"""How these tools identify themselves to the servers they fetch from.

Three of the tools make requests to somebody else's machine: tiles for the
route maps, elevation tiles for the voxel model, and Nominatim for geocoding
the places a write-up links to. All three providers ask, in their usage
policies, to be told who is calling - Nominatim will refuse a request with no
`User-Agent` at all, and rate-limits by it.

The default names the project and where to find it, which is enough for the
occasional run. Anything heavier should say who is actually doing it: set
`GPX_TOOLS_USER_AGENT` to something with a way of reaching you in it, so that a
provider with a problem can ask rather than simply block.
"""

import os

DEFAULT_USER_AGENT = "gpx-tools (+https://github.com/TokyoDanInJapan/gpx-tools)"


def user_agent():
    """The User-Agent to send, from `GPX_TOOLS_USER_AGENT` or the default.

    Read at each call rather than fixed at import, so setting the variable in a
    batch script reaches a tool that was already imported.
    """
    return os.environ.get("GPX_TOOLS_USER_AGENT") or DEFAULT_USER_AGENT
