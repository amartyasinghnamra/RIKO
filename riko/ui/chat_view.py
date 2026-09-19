"""
Chat view: a scrollable box of message bubbles plus an entry + send button.

Subscribes to "ai_token" / "ai_done" / "ai_error" from the bus. Because
those events can fire from a background thread (see ollama_client.py),
every UI update is marshalled onto the GTK main loop via GLib.idle_add.
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

from riko.core.bus import bus


class ChatView(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        # --- message history ---
        self.messages_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_child(self.messages_box)
        self.append(scroller)
        self._scroller = scroller

        # --- input row ---
        input_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.entry = Gtk.Entry()
        self.entry.set_hexpand(True)
        self.entry.set_placeholder_text("Message RIKO...")
        self.entry.connect("activate", self._on_send)

        send_button = Gtk.Button(label="Send")
        send_button.connect("clicked", self._on_send)

        input_row.append(self.entry)
        input_row.append(send_button)
        self.append(input_row)

        self._current_ai_label: Gtk.Label | None = None

        bus.subscribe("ai_token", lambda tok: GLib.idle_add(self._on_ai_token, tok))
        bus.subscribe("ai_done", lambda full: GLib.idle_add(self._on_ai_done, full))
        bus.subscribe("ai_error", lambda err: GLib.idle_add(self._on_ai_error, err))

    def _on_send(self, _widget) -> None:
        text = self.entry.get_text().strip()
        if not text:
            return
        self.entry.set_text("")
        self._add_message(f"You: {text}")
        self._current_ai_label = self._add_message("RIKO: ")
        bus.publish("user_message", text)

    def _add_message(self, text: str) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.set_wrap(True)
        label.set_xalign(0)
        self.messages_box.append(label)
        return label

    def _on_ai_token(self, token: str) -> bool:
        if self._current_ai_label is not None:
            self._current_ai_label.set_text(self._current_ai_label.get_text() + token)
        return False  # GLib.idle_add: run once

    def _on_ai_done(self, _full_text: str) -> bool:
        self._current_ai_label = None
        return False

    def _on_ai_error(self, message: str) -> bool:
        self._add_message(f"[error] {message}")
        self._current_ai_label = None
        return False
