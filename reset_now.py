"""DISABLED - this is the script that destroyed the real data on 2026-10-21.

On 2026-10-21 00:58:16 this file ran:

    DELETE FROM transactions WHERE id NOT IN (1,2)
    DELETE FROM budgets

against the production database 'money_tracker', with no confirmation, no
dry-run and a hard-coded database name. It deleted 4 real transactions
(ids 25-28) and the budget. The rows were recovered afterwards from the
binary log, but the script itself was never fixed - only used.

It is kept here for the record. Every statement that can change data is
commented out, and running the file now does nothing but print a warning.

If you ever really need to reset a database, use the safe route instead:

    python scripts\restore.py <dump> --target money_tracker_test --dry-run
    python scripts\restore.py <dump> --target money_tracker_test

or delete the rows through the app, where a delete can be undone.
"""

import sys

MESSAGE = """
reset_now.py is DISABLED.

It is the script that deleted real transactions from the 'money_tracker'
database on 2026-10-21. Its DELETE statements have been commented out and
will not run again.

To reset a database, restore a backup into a scratch database instead:

    python scripts\\restore.py <dump> --target money_tracker_test --dry-run
    python scripts\\restore.py <dump> --target money_tracker_test
"""


def main():
    print(MESSAGE)
    return 1


if __name__ == "__main__":
    sys.exit(main())
