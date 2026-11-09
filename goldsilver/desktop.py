"""Desktop chat client: ``python -m goldsilver.desktop``."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import scrolledtext

from .chatbot import respond
from .config import Settings
from .market import MarketDataError, MarketService, Quote

BACKGROUND = "#14161b"
SURFACE = "#1f2229"
TEXT = "#eceef2"
ACCENT = "#d9a93a"
QUICK_QUESTIONS = (
    ("Gold", "What is the gold price?"),
    ("Silver", "What is the silver price?"),
    ("Ratio", "What is the gold/silver ratio?"),
    ("Canada", "Prices in Canada"),
    ("India", "Prices in India"),
    ("$10,000", "How much gold for $10,000?"),
)


class ChatApp:
    def __init__(self, root: tk.Tk, market: MarketService) -> None:
        self.root = root
        self.market = market
        self.quote: Quote | None = None
        self.results: queue.Queue[Quote | None] = queue.Queue()

        root.title("Gold/Silver Assistant")
        root.geometry("560x680")
        root.configure(bg=BACKGROUND)

        self.summary = tk.Label(
            root, text="Loading prices…", bg=SURFACE, fg=ACCENT, font=("Segoe UI", 12, "bold")
        )
        self.summary.pack(fill="x", ipady=12)

        self.log = scrolledtext.ScrolledText(
            root, wrap=tk.WORD, bg=SURFACE, fg=TEXT, relief=tk.FLAT, font=("Segoe UI", 11)
        )
        self.log.pack(fill="both", expand=True, padx=12, pady=12)
        self.log.configure(state="disabled")

        quick = tk.Frame(root, bg=BACKGROUND)
        quick.pack(fill="x", padx=12)
        for index, (label, question) in enumerate(QUICK_QUESTIONS):
            tk.Button(
                quick, text=label, command=lambda q=question: self.ask(q), relief=tk.FLAT
            ).grid(row=0, column=index, sticky="ew", padx=2)
            quick.columnconfigure(index, weight=1)

        entry_row = tk.Frame(root, bg=BACKGROUND)
        entry_row.pack(fill="x", padx=12, pady=12)
        self.entry = tk.Entry(entry_row, font=("Segoe UI", 12))
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.bind("<Return>", lambda _event: self.submit())
        tk.Button(entry_row, text="Send", command=self.submit, relief=tk.FLAT).pack(
            side="right", padx=(8, 0), ipadx=12, ipady=4
        )
        self.entry.focus()

        self.append("Assistant", respond("help", None).to_text())
        threading.Thread(target=self.load_quote, daemon=True).start()
        self.poll_results()

    def load_quote(self) -> None:
        try:
            quote: Quote | None = self.market.latest()
        except MarketDataError:
            quote = None
        self.results.put(quote)

    def poll_results(self) -> None:
        """Apply background results here, because Tk may only be used from its own thread."""
        try:
            self.show_quote(self.results.get_nowait())
        except queue.Empty:
            self.root.after(100, self.poll_results)

    def show_quote(self, quote: Quote | None) -> None:
        self.quote = quote
        if quote is None:
            self.summary.configure(text="Prices unavailable")
        else:
            self.summary.configure(
                text=f"Gold ${quote.gold_usd:,.2f}   Silver ${quote.silver_usd:,.2f}   "
                f"Ratio {quote.ratio:.2f}:1"
            )

    def append(self, speaker: str, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert(tk.END, f"{speaker}\n{text}\n\n")
        self.log.configure(state="disabled")
        self.log.see(tk.END)

    def ask(self, question: str) -> None:
        self.append("You", question)
        self.append("Assistant", respond(question, self.quote).to_text())

    def submit(self) -> None:
        question = self.entry.get().strip()
        if question:
            self.entry.delete(0, tk.END)
            self.ask(question)


def main() -> None:
    root = tk.Tk()
    ChatApp(root, MarketService.from_settings(Settings.from_env()))
    root.mainloop()


if __name__ == "__main__":
    main()
