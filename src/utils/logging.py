import yaml
import logging.config
import traceback
from pathlib import Path

# Next to the code, not in the working directory: the app starts the same from the repo root and from the image.
SOURCE = Path(__file__).resolve().parent.parent
LOGGING_CONFIG = SOURCE / "logging_config.yaml"


def configure_logging():
    with open(LOGGING_CONFIG, 'rt') as f:
        config = yaml.safe_load(f.read())
    logging.config.dictConfig(config)


def describe(error: BaseException) -> str:
    """
        What goes to the log of a failure: its kind and the line it came from, never its text. The text of an
        exception repeats what it choked on: a phone number, a name, a token, the address it was calling.
    """
    frames = traceback.extract_tb(error.__traceback__)
    if not frames:
        return type(error).__name__
    # The last line of this service that was running, and the line that raised if it is in a library.
    ours = [frame for frame in frames if Path(frame.filename).is_relative_to(SOURCE)] or frames
    where = f"{Path(ours[-1].filename).name}:{ours[-1].lineno}"
    if frames[-1] is not ours[-1]:
        where += f" ({Path(frames[-1].filename).name}:{frames[-1].lineno})"
    return f"{type(error).__name__} at {where}"

