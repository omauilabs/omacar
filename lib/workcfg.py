"""Work's settings: which projects can be dispatched by voice, and where.

~/.config/omacar/work.toml, edited by the owner. A project that is not listed
here cannot be dispatched: the allowlist is the whole of Work's reach.
"""
import os
import tomllib
from dataclasses import dataclass, field

PATH = os.path.join(os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config")), "omacar", "work.toml")
HOSTS = ("tablet", "box")


@dataclass(frozen=True)
class Project:
    name: str
    names: tuple
    path: str
    gate: str
    network: tuple = ()   # domains the sandbox may reach; never GitHub


# The runner pushes and opens PRs itself; the agent must not be able to.
FORBIDDEN_DOMAINS = ("github.com", "api.github.com")


@dataclass(frozen=True)
class Config:
    host: str = "box"
    projects: dict = field(default_factory=dict)
    max_sessions: int = 2
    daily_cap: int = 6
    quiet: tuple = (21, 7)
    relay_url: str = "http://127.0.0.1:7570"
    tablet_node: str = "omacar-1"


def load(path=None):
    path = path or PATH
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        return Config()
    host = raw.get("host", "box")
    if host not in HOSTS:
        raise ValueError(f"work.toml: host must be one of {HOSTS}, not {host!r}")
    projects = {}
    for name, p in (raw.get("project") or {}).items():
        if not p.get("gate"):
            raise ValueError(f"work.toml: project {name} has no gate command")
        if not p.get("path"):
            raise ValueError(f"work.toml: project {name} has no path")
        network = tuple(str(d) for d in p.get("network", []))
        for d in network:
            if d.lower() in FORBIDDEN_DOMAINS:
                raise ValueError(f"work.toml: project {name} lists {d} in network; "
                                 "the runner talks to GitHub, the agent never does")
        names = [name.lower()] + [str(n).lower() for n in p.get("names", [])]
        projects[name] = Project(name, tuple(dict.fromkeys(names)),
                                 os.path.expanduser(p["path"]), str(p["gate"]), network)
    q = raw.get("quiet", [21, 7])
    return Config(host=host, projects=projects,
                  max_sessions=int(raw.get("max_sessions", 2)),
                  daily_cap=int(raw.get("daily_cap", 6)),
                  quiet=(int(q[0]), int(q[1])),
                  relay_url=str(raw.get("relay_url", Config.relay_url)),
                  tablet_node=str(raw.get("tablet_node", "omacar-1")))
