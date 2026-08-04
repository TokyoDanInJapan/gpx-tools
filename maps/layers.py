"""Tile layers for the route maps, and the API keys they need.

Split out of maps.mapgen so that choosing a layer is not entangled with drawing
a map: maps.multimap needs the same table and the same key lookup, and used to
reach into the map generator to get them.

Keys are read from the environment, or from a .env named by GPX_TOOLS_ENV or
sitting in the working directory. They never appear in output - the map carries
the provider's attribution text, not its key.
"""

import os

# Selectable tile layers. Layers with a "key_env" need a free API key from that
# provider, read from the named environment variable (or .env). Their "url"
# carries a {key} placeholder. Open layers omit "key_env". The "english" flag
# marks layers that render Latin/English labels where OpenStreetMap has them
# (name:en). The Thunderforest and plain OSM layers render local script, such
# as Japanese in Japan. Each entry carries the attribution stamped on the map.
LAYERS = {
    "opencyclemap": {
        "url": "https://tile.thunderforest.com/cycle/{z}/{x}/{y}.png?apikey={key}",
        "attribution": "Maps © Thunderforest, Data © OpenStreetMap contributors",
        "key_env": "THUNDERFOREST_API_KEY",
    },
    "transport": {
        "url": "https://tile.thunderforest.com/transport/{z}/{x}/{y}.png?apikey={key}",
        "attribution": "Maps © Thunderforest, Data © OpenStreetMap contributors",
        "key_env": "THUNDERFOREST_API_KEY",
    },
    "landscape": {
        "url": "https://tile.thunderforest.com/landscape/{z}/{x}/{y}.png?apikey={key}",
        "attribution": "Maps © Thunderforest, Data © OpenStreetMap contributors",
        "key_env": "THUNDERFOREST_API_KEY",
    },
    "outdoors": {
        "url": "https://tile.thunderforest.com/outdoors/{z}/{x}/{y}.png?apikey={key}",
        "attribution": "Maps © Thunderforest, Data © OpenStreetMap contributors",
        "key_env": "THUNDERFOREST_API_KEY",
    },
    "maptiler": {
        "url": "https://api.maptiler.com/maps/streets-v2/{z}/{x}/{y}.png?key={key}&language=en",
        "attribution": "© MapTiler © OpenStreetMap contributors",
        "key_env": "MAPTILER_API_KEY",
        "english": True,
        "tile_size": 512,  # MapTiler v2 styles serve 512px tiles, not 256
    },
    "geoapify": {
        "url": "https://maps.geoapify.com/v1/tile/osm-bright/{z}/{x}/{y}.png?apiKey={key}&lang=en",
        "attribution": "Powered by Geoapify | © OpenMapTiles © OpenStreetMap contributors",
        "key_env": "GEOAPIFY_API_KEY",
        "english": True,
    },
    "osm": {
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": "© OpenStreetMap contributors",
    },
    "opentopomap": {
        "url": "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attribution": ("Map data: © OpenStreetMap contributors, SRTM | "
                        "Map style: © OpenTopoMap (CC-BY-SA)"),
    },
}

DEFAULT_LAYER = "opencyclemap"


def resolve_layer(name, api_key):
    """Return (url_template, attribution) for a named layer.

    Substitutes the provider API key for layers that need one, raising
    ValueError if such a layer is requested without a key. `api_key` may be an
    explicit override. Otherwise the key is resolved from the layer's env var
    or from the environment and .env (see `env_value`).
    """
    layer = LAYERS[name]
    url = layer["url"]
    key_env = layer.get("key_env")
    if key_env:
        key = api_key or env_value(key_env)
        if not key:
            raise ValueError(
                f"Layer '{name}' needs an API key. Pass --api-key, set {key_env}, "
                f"or add {key_env}=... to a .env in this directory."
            )
        url = url.replace("{key}", key)
    return url, layer["attribution"], layer.get("tile_size", 256)


def env_file():
    """Where to look for a .env, or None if there is nothing to look at.

    `GPX_TOOLS_ENV` names the file outright. Otherwise it is `.env` in the
    working directory. Both are deliberate: an installed package has no repo
    root to hang a dotfile off, and guessing one - the source tree it was
    installed from, the user's home - would read a file the caller never meant
    to offer. Running from a project directory is the ordinary case and picks
    its own keys up. Naming the file covers the rest.
    """
    named = os.environ.get("GPX_TOOLS_ENV")
    if named:
        return named
    local = os.path.join(os.getcwd(), ".env")
    return local if os.path.exists(local) else None


def env_value(name):
    """Resolve a named secret from the environment or a .env file.

    Precedence: the environment variable wins, then the .env (see `env_file`).
    Neither is ever printed - the map carries the provider's attribution, not
    its key - so secrets stay out of logs and out of the images.
    """
    value = os.environ.get(name)
    if value:
        return value
    path = env_file()
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(f"{name}="):
                    return line.split("=", 1)[1].strip().strip("'\"") or None
    except OSError:
        pass
    return None
