from concurrent.futures import ThreadPoolExecutor
from threading import Event

from agent.ownership import ClaimOwnership


def test_pending_resume_owns_its_claim_before_background_execution():
    ownership = ClaimOwnership()
    assert ownership.request_resume("one")
    assert not ownership.request_resume("one")
    with ownership.dispatch("one") as skipped:
        assert not skipped
    with ownership.dispatch("two") as independent:
        assert independent
    with ownership.resume("one"), ownership.dispatch("one") as still_skipped:
        assert not still_skipped
    with ownership.dispatch("one") as released:
        assert released


def test_resume_waits_until_current_dispatch_releases_claim():
    ownership = ClaimOwnership()
    trying, entered = Event(), Event()

    def resume():
        trying.set()
        with ownership.resume("one"):
            entered.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with ownership.dispatch("one") as owned:
            assert owned
            assert ownership.request_resume("one")
            completion = executor.submit(resume)
            assert trying.wait(timeout=3)
            assert not entered.is_set()
        completion.result(timeout=3)
    assert entered.is_set()
