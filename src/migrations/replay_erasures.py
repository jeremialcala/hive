# -*- coding: utf-8 -*-
"""
    Makes again the deletions written down in the register of erasures. It is what has to run after a backup of
    the database is restored: the backup brings back every contact, chatbot and client deleted after it was
    taken (ADR-0014).

    It goes through the whole register, oldest first. A deletion whose data is not there is left alone, so
    running it on a database that was not restored, or running it twice, changes nothing. Nothing it deletes is
    written down again.

    It only reports what it would do unless it is called with --apply.

        python -m migrations.replay_erasures            # report
        python -m migrations.replay_erasures --apply    # delete
"""
import sys

from classes import Client, Contact, ConversationMessage
from repository import TenantRepository, erase_client, erasures


def came_back(entry: dict) -> int:
    """How many documents of something that was deleted are in the database again."""
    repo = TenantRepository(entry["clientId"])
    if entry["kind"] == erasures.CLIENT:
        return Client.objects(uuid=entry["clientId"]).count() + repo.stored()
    if entry["kind"] == erasures.CHATBOT:
        return repo.stored_for_bot(entry["botId"])
    # The contacts and their conversations: one can come back without the other.
    who = {"clientId": entry["clientId"], "botId": entry["botId"]}
    return (Contact.objects(**who, _uuid__in=entry["contacts"]).count()
            + ConversationMessage.objects(**who, contactId__in=entry["contacts"]).count())


def erase_again(entry: dict) -> None:
    repo = TenantRepository(entry["clientId"])
    if entry["kind"] == erasures.CLIENT:
        erase_client(entry["clientId"])
    elif entry["kind"] == erasures.CHATBOT:
        repo.erase_bot(entry["botId"])
    else:
        repo.erase_contacts(entry["botId"], entry["contacts"])


def run(apply: bool) -> dict:
    recorded, unreadable = erasures.entries()
    report = {kind: {"recorded": 0, "back": 0, "documents": 0}
              for kind in (erasures.CONTACT, erasures.CHATBOT, erasures.CLIENT)}
    for entry in recorded:
        if entry["kind"] not in report:
            unreadable += 1
            continue
        counts = report[entry["kind"]]
        counts["recorded"] += 1
        documents = came_back(entry)
        if documents:
            counts["back"] += 1
            counts["documents"] += documents
            if apply:
                erase_again(entry)
    report["unreadable lines"] = unreadable
    return report


if __name__ == "__main__":
    from classes import Settings

    erasures.configure(Settings().erasures_dir)
    apply = "--apply" in sys.argv[1:]
    try:
        report = run(apply)
    except erasures.RegisterUnavailable as e:
        sys.exit(f"The register of erasures cannot be read ({e}): check ERASURES_DIR. Nothing was deleted.")
    for kind in (erasures.CONTACT, erasures.CHATBOT, erasures.CLIENT):
        counts = report[kind]
        done = " -> deleted again" if apply and counts["back"] else ""
        print(f"{kind}: {counts['recorded']} deletions recorded; {counts['back']} are back in the database, "
              f"with {counts['documents']} documents{done}")
    if report["unreadable lines"]:
        print(f"{report['unreadable lines']} lines of the register could not be read")
    if not apply:
        print("Nothing was deleted. Run again with --apply to delete what came back.")
