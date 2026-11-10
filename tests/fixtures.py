"""Shared source-snapshot fixture for behavior tests."""
import sqlite3
from pathlib import Path


def make_source(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE tag_types (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        INSERT INTO tag_types VALUES (1, 'general'), (4, 'artist');

        CREATE TABLE tags (
            id INTEGER PRIMARY KEY,
            tag TEXT NOT NULL,
            type_id INTEGER NOT NULL,
            category_id INTEGER,
            post_count INTEGER NOT NULL
        );
        INSERT INTO tags VALUES
            (1, 'looking_at_viewer', 1, NULL, 100000),
            (2, 'long_hair', 1, NULL, 50000),
            (3, 'hair_band', 1, NULL, 200),
            (4, 'hair_ornament', 1, NULL, 300),
            (5, 'peeing_on_viewer', 1, NULL, 200000);

        CREATE TABLE tag_wiki (id INTEGER PRIMARY KEY, tag_id INTEGER, wiki_content TEXT);
        INSERT INTO tag_wiki VALUES
            (1, 1, 'A character looks directly at the viewer.'),
            (2, 2, 'Hair that extends well past the shoulders.'),
            (3, 5, 'A character is directly peeing on where the viewer looks.');

        CREATE TABLE tag_aliases (id INTEGER PRIMARY KEY, tag_id INTEGER, alias TEXT);
        INSERT INTO tag_aliases VALUES
            (1, 2, 'very long hair'),
            (2, 3, 'hair accessory'),
            (3, 4, 'Hair Accessory');

        CREATE TABLE main_table (post_id INTEGER PRIMARY KEY, created_at TEXT, updated_at TEXT);
        INSERT INTO main_table VALUES (12, '2026-08-23T10:00:00Z', '2026-08-23T10:00:00Z');
        """
    )
    conn.commit()
    conn.close()
