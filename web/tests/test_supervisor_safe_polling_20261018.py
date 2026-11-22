"""Live Supervisor reads must preserve the document and its composer."""
from tm_observer.supervisor_view import _refresh_script


def test_updates_are_local_and_keep_drafts_and_attachments():
    script = _refresh_script(active=True, waiting=False)
    assert 'const delay=8000;' in script
    assert 'location.reload' not in script
    assert 'await fetch' in script
    assert "document.querySelector('details[open]')" in script
    assert 'const hasDraft=' in script and 'const editing=' in script
    assert 'attachments?.attachments?.length' in script
    assert 'finally{setTimeout(poll,delay);}' in script
    assert _refresh_script(active=False,waiting=False)==''
    assert 'const delay=3000;' in _refresh_script(active=True,waiting=True)
