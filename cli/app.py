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
    CONCURRENCY_MAX,
    OUT_DIR,
    PROVIDER_PRESETS,
    get_api_key,
    get_base_url,
    get_concurrency,
    get_extra_body,
    get_fill_thinking,
    get_max_retries,
    get_model,
    get_provider,
    get_provider_info,
    get_token_budget,
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

        try:
            result = translate_mod.run_translation(book, on_progress=on_progress)
        except translate_mod.BudgetExceeded as err:
            console.print(
                f"[yellow]Budget exceeded: used {err.used:,} of {err.budget:,} tokens — stopped. "
                "Cache kept; re-run to resume.[/yellow]"
            )
            return
    if result.get("cache_cleared"):
        console.print("[yellow]Note: translation cache was cleared (thinking/fill mode changed).[/yellow]")
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
        console.print("[yellow]Set your API key first (Settings).[/yellow]")
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
        info = get_provider_info()
        thinking = get_extra_body().get("thinking", {}).get("type", "n/a")
        console.print(
            Panel(
                "[bold]Settings[/bold]\n"
                f"Provider: {get_provider()} ({info['label']})\n"
                f"API key: {masked}\n"
                f"Model: {get_model()}   API: {get_base_url()}\n"
                f"Parallel requests: {get_concurrency()}   Mode: {thinking}\n"
                f"Fill mode: {get_fill_thinking()}   Retries: {get_max_retries()}\n"
                f"Token budget: {get_token_budget('book.epub'):,} (test: {get_token_budget('Test_.epub'):,})"
            )
        )
        choice = questionary.select(
            "Actions",
            choices=[
                "Change provider",
                "Set / change API key",
                "Change model",
                "Change base URL",
                "Change concurrency",
                "Change mode (speed vs. quality)",
                "Change fill mode (structure mapping)",
                "Change token budget",
                "Change retries (fill + API)",
                "Back",
            ],
        ).ask()
        if choice == "Change provider":
            new_provider = questionary.select(
                "Provider",
                choices=[
                    questionary.Choice(title=preset["label"], value=name)
                    for name, preset in PROVIDER_PRESETS.items()
                ],
            ).ask()
            if new_provider:
                settings = load_settings()
                settings["provider"] = new_provider
                save_settings(settings)
                console.print(f"[green]Provider switched to {new_provider}.[/green]")
        elif choice == "Set / change API key":
            info = get_provider_info()
            new_key = questionary.text(
                f"{info['label']} API key (env var {info['env_key']} also works):"
            ).ask()
            if new_key:
                set_api_key(new_key)
                console.print("[green]API key saved.[/green]")
        elif choice == "Change model":
            info = get_provider_info()
            if info["models"]:
                new_model = questionary.select(
                    "Model", choices=list(info["models"])
                ).ask()
            else:
                new_model = questionary.text(
                    "Model name (any OpenAI-compatible model):", default=get_model()
                ).ask()
            if new_model:
                settings = load_settings()
                settings["model"] = str(new_model).strip()
                save_settings(settings)
                console.print(f"[green]Model set to {settings['model']}.[/green]")
        elif choice == "Change base URL":
            new_url = questionary.text(
                "API base URL (blank = provider default):",
                default=get_base_url(),
            ).ask()
            if new_url is not None:
                settings = load_settings()
                settings["base_url"] = new_url.strip()
                save_settings(settings)
        elif choice == "Change concurrency":
            new_value = questionary.text(
                f"Parallel requests (1-{CONCURRENCY_MAX}, default 8):",
                default=str(get_concurrency()),
            ).ask()
            if new_value and new_value.isdigit() and 1 <= int(new_value) <= CONCURRENCY_MAX:
                settings = load_settings()
                settings["concurrency"] = int(new_value)
                save_settings(settings)
                console.print("[green]Concurrency saved.[/green]")
            elif new_value:
                console.print(f"[yellow]Concurrency must be 1-{CONCURRENCY_MAX}.[/yellow]")
        elif choice == "Change mode (speed vs. quality)":
            new_mode = questionary.select(
                "Mode",
                choices=[
                    questionary.Choice(title="Fast (no thinking) — recommended", value="disabled"),
                    questionary.Choice(title="Accurate (thinking on) — slower", value="enabled"),
                ],
            ).ask()
            if new_mode:
                settings = load_settings()
                settings["thinking"] = new_mode
                save_settings(settings)
                console.print("[green]Mode saved.[/green]")
        elif choice == "Change fill mode (structure mapping)":
            new_mode = questionary.select(
                "Fill mode",
                choices=[
                    questionary.Choice(title="Adaptive (recommended)", value="adaptive"),
                    questionary.Choice(title="Thinking on — most accurate", value="enabled"),
                    questionary.Choice(title="No thinking — fastest", value="disabled"),
                ],
            ).ask()
            if new_mode:
                settings = load_settings()
                settings["fill_thinking"] = new_mode
                save_settings(settings)
                console.print(
                    "[green]Fill mode saved.[/green] "
                    "[yellow]Changing it clears the book's translation cache — "
                    "the next translate re-does the full book.[/yellow]"
                )
        elif choice == "Change token budget":
            settings = load_settings()
            new_value = questionary.text(
                "Token budget for normal books (default 1500000):",
                default=str(get_token_budget("book.epub")),
            ).ask()
            if new_value and new_value.isdigit():
                settings["token_budget"] = int(new_value)
            test_value = questionary.text(
                "Token budget for test books (Test_ prefix or _test suffix, default 500000):",
                default=str(get_token_budget("Test_.epub")),
            ).ask()
            if test_value and test_value.isdigit():
                settings["token_budget_test"] = int(test_value)
            save_settings(settings)
            console.print("[green]Token budgets saved.[/green]")
        elif choice == "Change retries (fill + API)":
            new_value = questionary.text(
                "Retries per request (default 2, applies to both fill + API):",
                default=str(get_max_retries()),
            ).ask()
            if new_value and new_value.isdigit():
                settings = load_settings()
                settings["max_retries"] = int(new_value)
                settings["retry_times"] = int(new_value)
                save_settings(settings)
                console.print("[green]Retries saved.[/green]")
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
        info = get_provider_info()
        console.print(
            f"[yellow]Welcome! Before translating, set your API key for "
            f"{info['label']} (provider '{get_provider()}', env var "
            f"{info['env_key']}).[/yellow]"
        )
        new_key = questionary.text(f"{info['label']} API key:").ask()
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
