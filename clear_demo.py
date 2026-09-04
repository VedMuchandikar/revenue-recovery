#!/usr/bin/env python3
"""Clear demo data from the database."""

import sqlite3

def clear_demo_data():
    conn = sqlite3.connect('revenue_recovery.db')
    cursor = conn.cursor()

    # Clear revenue_events by id
    cursor.execute('DELETE FROM revenue_events WHERE id LIKE ?', ('demo-%',))
    print(f"Cleared revenue_events")

    # Clear other tables by event_id
    tables = [
        'diagnoses',
        'proposed_actions',
        'guardrail_checks',
        'action_results',
        'outcomes',
        'audit_logs'
    ]

    for table in tables:
        cursor.execute(f'DELETE FROM {table} WHERE event_id LIKE ?', ('demo-%',))
        print(f"Cleared {table}")

    conn.commit()
    conn.close()
    print("All demo data cleared!")

if __name__ == "__main__":
    clear_demo_data()