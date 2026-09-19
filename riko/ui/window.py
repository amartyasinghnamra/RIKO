import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from riko.ui.chat_view import ChatView
from riko.config.settings import settings


class RikoWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application):
        super().__init__(application=application, title=settings.app_title)
        self.set_default_size(settings.window_width, settings.window_height)
        self.set_child(ChatView())
