from pathlib import Path

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

import qa_check as qa_mod
import scan_glossary as scan_mod
import translate_book as translate_mod
from config import (
    BOOKS_DIR,
    OUT_DIR,
    get_api_key,
    get_base_url,
    get_concurrency,
    get_model,
    load_settings,
    save_settings,
    set_api_key,
)
from glossary import (
    GLOBAL_NAME,
    add_terms,
    book_key,
    load_glossary,
    merge_glossaries,
    save_glossary,
)

console = Console()


def list_books() -> list:
    return sorted(p for p in BOOKS_DIR.glob("*.epub"))


def list_translated() -> list:
    return sorted(p for p in OUT_DIR.glob("*.epub"))


def pick_book(prompt: str):
    books = list_books()
    if not books:
        console.print("[yellow]No .epub files found. Put your books in the 'books' folder.[/yellow]")
        return None
    choices = [questionary.Choice(title=book.name, value=book) for book in books]
    choices.append(questionary.Choice(title="(cancel)", value=None))
    return questionary.select(prompt, choices=choices).ask()


def run_translation_with_progress(book: Path) -> None:
    progress = Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        TextColumn("{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    )
    with progress:
        task = progress.add_task(f"Translating {book.name}", total=100)

        def on_progress(frac: float) -> None:
            progress.update(task, completed=min(100.0, frac * 100))

        result = translate_mod.run_translation(book, on_progress=on_progress)
    console.print(f"[green]Done![/green]  Saved to: {result['target']}")
    console.print(
        f"Tokens: {result['input_tokens']:,} in / {result['output_tokens']:,} out   "
        f"Est. cost: ${result['cost']:.2f}"
    )


def translate_flow() -> None:
    book = pick_book("Which book do you want to translate?")
    if not book:
        return
    if not get_api_key():
        console.print("[yellow]Set your OpenCode Go API key first (Settings).[/yellow]")
        return
    key = book_key(book.name)
    est = translate_mod.estimate(book)
    console.print(
        Panel(
            f"[bold]{book.name}[/bold]\n"
            f"Chapters: {est['chapters']}   Est. tokens: ~{est['tokens']:,}\n"
            f"Est. cost: ~${est['cost']:.2f}  (model: {get_model()})\n"
            f"Glossary: shared ({len(load_glossary(GLOBAL_NAME))}) + this book ({len(load_glossary(key))})"
        )
    )
    if questionary.confirm("Start translation?").ask():
        run_translation_with_progress(book)


def edit_glossary(scope: str) -> None:
    while True:
        terms = load_glossary(scope)
        title = "Shared glossary (all books)" if scope == GLOBAL_NAME else f"Glossary for: {scope}"
        console.print(Panel(f"[bold]{title}[/bold]  ({len(terms)} terms)"))
        if terms:
            for i, (src, dst) in enumerate(terms.items(), 1):
                console.print(f"  {i}. {src}  ->  {dst}")
        else:
            console.print("  (empty)")
        action = questionary.select(
            "Actions",
            choices=["Add a term", "Edit a term", "Delete a term", "Back"],
        ).ask()
        if action == "Add a term":
            src = questionary.text("Chinese term (e.g. 丹田):").ask()
            if src:
                dst = questionary.text(f"English translation for '{src}':").ask()
                if dst:
                    added = add_terms(scope, {src: dst})
                    console.print(f"[green]Added {added} term(s).[/green]")
        elif action == "Edit a term":
            if not terms:
                continue
            choices = [questionary.Choice(title=f"{src} -> {dst}", value=src) for src, dst in terms.items()]
            selected = questionary.select("Which term to edit?", choices=choices).ask()
            if selected:
                dst = questionary.text(
                    f"New English translation for '{selected}':", default=terms[selected]
                ).ask()
                if dst:
                    terms[selected] = dst
                    save_glossary(scope, terms)
                    console.print("[green]Saved.[/green]")
        elif action == "Delete a term":
            if not terms:
                continue
            choices = [questionary.Choice(title=f"{src} -> {dst}", value=src) for src, dst in terms.items()]
            selected = questionary.select("Which term to delete?", choices=choices).ask()
            if selected:
                del terms[selected]
                save_glossary(scope, terms)
                console.print("[green]Deleted.[/green]")
        else:
            return


def glossary_flow() -> None:
    books = list_books()
    if not books:
        console.print("[yellow]No books yet. Put .epub files in the 'books' folder.[/yellow]")
        return
    choices = [questionary.Choice(title="Shared glossary (all books)", value=GLOBAL_NAME)]
    choices.extend(
        questionary.Choice(title=book.name, value=book_key(book.name)) for book in books
    )
    scope = questionary.select("Which glossary do you want to manage?", choices=choices).ask()
    if scope:
        edit_glossary(scope)


def scan_flow() -> None:
    book = pick_book("Which book should I scan for new terms?")
    if not book:
        return
    if not get_api_key():
        console.print("[yellow]Set your API key first (Settings).[/yellow]")
        return
    console.print("[cyan]Scanning the book for names, skills, and terms...[/cyan]")
    candidates = scan_mod.candidate_terms(book, min_count=5, max_terms=60)
    if not candidates:
        console.print("No candidate terms found.")
        return
    console.print(f"[cyan]Asking the model to propose {len(candidates)} translations...[/cyan]")
    proposed = scan_mod.propose_translations(
        list(candidates.keys()), get_api_key(), get_base_url(), get_model()
    )
    if not proposed:
        console.print("[red]The model returned no usable suggestions. Try again.[/red]")
        return
    scope = book_key(book.name)
    existing = merge_glossaries(scope)
    fresh = {src: dst for src, dst in proposed.items() if src not in existing}
    if not fresh:
        console.print("All candidate terms are already in the glossary.")
        return
    choices = [
        questionary.Choice(title=f"{src}  ->  {dst}", value=(src, dst), checked=True)
        for src, dst in fresh.items()
    ]
    picked = questionary.checkbox("Which suggested terms should I add?", choices=choices).ask()
    if picked:
        added = add_terms(scope, dict(picked))
        console.print(f"[green]Added {added} term(s) to the '{scope}' glossary.[/green]")


def qa_flow() -> None:
    translated_books = list_translated()
    if not translated_books:
        console.print("[yellow]No translated books in the 'out' folder yet.[/yellow]")
        return
    choices = [questionary.Choice(title=book.name, value=book) for book in translated_books]
    target = questionary.select("Which translated book should I check?", choices=choices).ask()
    if not target:
        return
    source = next(
        (s for s in list_books() if book_key(s.name) == book_key(target.name)), None
    )
    if not source:
        console.print("[yellow]Could not find the matching source book in 'books'.[/yellow]")
        return
    console.print("[cyan]Checking glossary consistency across chapters...[/cyan]")
    issues = qa_mod.check(source, target)
    if not issues:
        console.print("[green]No consistency issues found.[/green]")
        return
    console.print(f"[yellow]{len(issues)} potential issue(s) found:[/yellow]")
    for src, dst, chapter, variant, count in issues[:40]:
        console.print(
            f"  Ch {chapter}: '{src}' expected '{dst}' but may appear as '{variant}' (x{count})"
        )
    if len(issues) > 40:
        console.print(f"  ... showing first 40 of {len(issues)}")
    console.print(
        "Tip: if the variant is fine, add it under Manage glossary. "
        "If not, fix the glossary and re-translate the book."
    )


def settings_flow() -> None:
    while True:
        key = get_api_key()
        masked = (key[:6] + "..." + key[-4:]) if len(key) > 12 else "(empty)"
        console.print(
            Panel(
                "[bold]Settings[/bold]\n"
                f"API key: {masked}\n"
                f"Model: {get_model()}   Parallel requests: {get_concurrency()}"
            )
        )
        choice = questionary.select(
            "Actions",
            choices=["Set / change API key", "Change model", "Change concurrency", "Back"],
        ).ask()
        if choice == "Set / change API key":
            new_key = questionary.text(
                "OpenCode Go API key (from opencode.ai/auth):"
            ).ask()
            if new_key:
                set_api_key(new_key)
                console.print("[green]API key saved.[/green]")
        elif choice == "Change model":
            new_model = questionary.select(
                "Model", choices=["deepseek-v4-flash", "deepseek-v4-pro"]
            ).ask()
            if new_model:
                settings = load_settings()
                settings["model"] = new_model
                save_settings(settings)
        elif choice == "Change concurrency":
            new_value = questionary.text(
                "Parallel requests (1-8, default 4):", default=str(get_concurrency())
            ).ask()
            if new_value and new_value.isdigit():
                settings = load_settings()
                settings["concurrency"] = int(new_value)
                save_settings(settings)
        else:
            return


def main_menu() -> None:
    while True:
        books = list_books()
        api_state = "API key: set" if get_api_key() else "API key: NOT set"
        console.print(
            Panel(
                f"[bold]Web Novel EPUB Translator[/bold]  (Chinese -> English)\n"
                f"Books in 'books' folder: {len(books)}   {api_state}"
            )
        )
        choice = questionary.select(
            "What do you want to do?",
            choices=[
                "Translate a book",
                "Manage glossary",
                "Scan a book for new terms",
                "Check translation quality",
                "Settings",
                "Quit",
            ],
        ).ask()
        if choice == "Translate a book":
            translate_flow()
        elif choice == "Manage glossary":
            glossary_flow()
        elif choice == "Scan a book for new terms":
            scan_flow()
        elif choice == "Check translation quality":
            qa_flow()
        elif choice == "Settings":
            settings_flow()
        else:
            console.print("Goodbye!")
            return


def main() -> None:
    console.clear()
    if not get_api_key():
        console.print(
            "[yellow]Welcome! Before translating, set your OpenCode Go API key "
            "(get one at opencode.ai/auth).[/yellow]"
        )
        new_key = questionary.text("OpenCode Go API key:").ask()
        if new_key:
            set_api_key(new_key)
            console.print("[green]API key saved.[/green]")
        else:
            console.print("[yellow]Skipped — you can set it later in Settings.[/yellow]")
    try:
        main_menu()
    except KeyboardInterrupt:
        console.print("\nGoodbye!")


if __name__ == "__main__":
    main()
