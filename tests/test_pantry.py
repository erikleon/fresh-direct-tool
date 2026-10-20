import os
from datetime import date

import pytest

from app import pantry
from app.analytics import Prediction


@pytest.mark.parametrize('listed, line, matches', [
    ('olive oil', 'olive oil', True),
    ('cocoa powder', 'cocoa powder', True),
    ('onion', 'organic onion', True),
    ('flour', 'self-rising flour', False),
    ('butter', 'peanut butter', False),
    ('olive oil', 'extra virgin olive oil', False),
])
def test_matching_is_exact_after_dropping_descriptors(listed, line, matches):
    assert pantry.is_confirmed(line, [pantry.terms(listed)]) is matches


def test_file_skips_comments_blank_lines_and_descriptor_only_names(tmp_path):
    path = tmp_path / 'pantry.txt'
    path.write_text('# on hand\n\nolive oil\n  organic  \n')
    items, note = pantry.load_confirmed(path)
    assert items == [pantry.terms('olive oil')]
    assert note is None


def test_unset_setting_is_a_silent_no_op():
    assert pantry.load_confirmed(None) == ([], None)


def test_file_older_than_90_days_is_not_used(tmp_path):
    path = tmp_path / 'pantry.txt'
    path.write_text('olive oil\n')
    old = path.stat().st_mtime - 91 * 86400
    os.utime(path, (old, old))
    items, note = pantry.load_confirmed(path)
    assert items == []
    assert '91 days ago' in note


def pred(days_since_last, interval=10):
    return Prediction(
        key='k', name='k', times_bought=5, mean_interval_days=interval,
        last_purchased=date(2026, 10, 1), days_since_last=days_since_last,
        predicted_next=date(2026, 10, 11), days_overdue=0, active=True)


@pytest.mark.parametrize('days, expected', [(6, True), (6.9, True), (7, False), (12, False)])
def test_on_hand_cutoff_is_seventy_percent_of_the_interval(days, expected):
    assert pantry.likely_on_hand(pred(days)) is expected
