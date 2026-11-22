"""Build report fixture tables from Sciver's authoritative SQLite schema."""
import sqlite3
from theory_master.science_graph_store import ScienceGraphStore

def _schema():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    store = ScienceGraphStore.__new__(ScienceGraphStore)
    store.db = connection
    store._schema()
    sql = "\n".join(row[0] + ";" for row in connection.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"))
    connection.close()
    return sql

SQLITE_SCHEMA = _schema()
