import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from riko.ui.window import RikoWindow

# Importing this registers the OllamaClient as a bus subscriber
from riko.llm import ollama_client  # noqa: F401


class RikoApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.amartya.riko")

    def do_activate(self) -> None:
        window = RikoWindow(self)
        window.present()


def main() -> int:
    app = RikoApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
