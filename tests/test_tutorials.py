"""Tutorials: every step's "Show me" finds what it points at, and finishing a lesson is remembered."""
from ui.help.lessons import LESSONS
from tests.test_application import make_home, start


def test_every_tutorial_step_points_at_something_real(qapp, tmp_path):
    app = start(qapp, make_home(tmp_path))
    w = app.main_window
    page, guide = w.tutorials, w.tutorials.guide
    try:
        assert w.pages.currentWidget() is page  # first start opens the tutorials
        missing = []
        for lesson in LESSONS:
            page.start(lesson.id)
            if lesson.id == "custom":
                w.custom_blocks.create("steps")  # the lesson has the user press + first
            for i, step in enumerate(lesson.steps):
                guide.go(i)
                qapp.processEvents()
                if step.target and not guide.show_me():
                    missing.append(f"{lesson.id}[{i}] {step.target}")
                if step.page:
                    assert w.sidebar.index_of(step.page) == w.pages.currentIndex(), step.page
            assert guide.isVisible() and guide.next_btn.text().startswith("Finish")
            guide.next()
            assert not guide.isVisible()
        assert missing == []
        assert page.done() == [lesson.id for lesson in LESSONS]
        assert w.pages.currentWidget() is page
    finally:
        w.custom_blocks._original = None
        w.editor._clean = w.editor.scene.wf.dumps()
        w.close()
        app._shutdown()
