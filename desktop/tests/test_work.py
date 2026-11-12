"""Work tab: rotation filling, re-sorting, deleting from a date, the current/next task."""
from datetime import date, timedelta

from app.core import work as w


def _proj(**kw):
    return w.WorkProject(name="청소", members=["A", "B", "C"], freq="weekly", **kw)


def test_dates_by_frequency():
    d = date(2026, 11, 6)  # a Friday
    assert w.dates(d, "weekly", 3) == [date(2026, 11, 6), date(2026, 11, 13), date(2026, 11, 20)]
    assert w.dates(d, "alternate", 2) == [d, date(2026, 11, 8)]
    assert w.dates(d, "weekdays", 3) == [d, date(2026, 11, 9), date(2026, 11, 10)]  # skips the weekend
    assert w.dates(date(2026, 11, 7), "weekdays", 1) == [date(2026, 11, 9)]
    assert w.dates(date(2026, 1, 31), "monthly", 3) == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]
    assert w.dates(d, "biweekly", 2)[1] == date(2026, 11, 20)


def test_fill_from_first_person_and_resort():
    p = _proj()
    tasks = w.fill_rotation(p, [], "B", date(2026, 11, 2), count=5)
    assert [t.assignee for t in w.of_project(tasks, p.id)] == ["B", "C", "A", "B", "C"]
    cur, nxt = w.current_next(tasks, p.id)
    assert (cur.assignee, nxt.assignee) == ("B", "C")
    tasks = w.set_done(tasks, cur.id)
    cur, nxt = w.current_next(tasks, p.id)
    assert (cur.assignee, nxt.assignee) == ("C", "A")  # the highlight moves on
    # someone moved the 3rd one and swapped a person; re-sort from it puts the order back
    third = w.of_project(tasks, p.id)[2]
    tasks = w.assign(w.move(tasks, third.id, date(2026, 11, 18)), third.id, "C")
    tasks = w.resort_from(p, tasks, date(2026, 11, 16))
    open_ = [t for t in w.of_project(tasks, p.id) if t.day >= date(2026, 11, 16)]
    assert [(t.day.day, t.assignee) for t in open_] == [(18, "C"), (25, "A"), (2, "B")]
    # delete from a date keeps the done record and the earlier ones
    tasks = w.delete_from(p.id, tasks, date(2026, 11, 20))
    assert [t.day.day for t in w.of_project(tasks, p.id)] == [2, 9, 18]


def test_turn_shared_by_two_people(tmp_path):
    p = _proj(per_turn=2)
    tasks = w.fill_rotation(p, [], "B", date(2026, 11, 2), count=4)
    assert [t.people for t in w.of_project(tasks, p.id)] == [["B", "C"], ["A", "B"], ["C", "A"], ["B", "C"]]
    t = w.of_project(tasks, p.id)[0]
    tasks = w.toggle_person(tasks, t.id, "C")
    assert next(x for x in tasks if x.id == t.id).people == ["B"]
    tasks = w.add_person(tasks, t.id, "A")
    assert next(x for x in tasks if x.id == t.id).people == ["B", "A"]
    path = tmp_path / "w.xlsx"
    w.write_work(path, [p], tasks)
    ps, ts = w.read_work(path)
    assert ps[0].per_turn == 2 and w.of_project(ts, p.id)[1].people == ["A", "B"]
    # more per turn than people: everyone once
    assert w.fill_rotation(_proj(per_turn=5), [], "A", date(2026, 11, 2), count=1)[0].people == ["A", "B", "C"]


def test_members_reorder_and_swap():
    assert w.reorder(["A", "B", "C"], "C", 0) == ["C", "A", "B"]
    p = _proj()
    t = w.fill_rotation(p, [], "A", date(2026, 11, 2), count=2)
    a, b = w.of_project(t, p.id)
    t = w.swap_people(t, a.id, b.id)
    assert [x.assignee for x in w.of_project(t, p.id)] == ["B", "A"]


def test_workbook_round_trip(tmp_path):
    p = _proj(task_title="실험실 청소")
    m = w.WorkProject(name="논문", kind="milestone", members=["A", "B"])
    tasks = w.fill_rotation(p, [], "A", date(2026, 11, 2), count=3)
    tasks.append(w.WorkTask(project_id=m.id, date="2026-12-01", assignee="B", title="초고"))
    tasks = w.set_done(tasks, tasks[0].id)
    board = w.WorkBoard(None, tmp_path, local=tmp_path / "업무.xlsx")
    board.save(lambda _p, _t: ([p, m], tasks))
    projects, again = w.read_work(tmp_path / "업무.xlsx")
    assert [(x.name, x.kind, x.members, x.freq) for x in projects] == [
        ("청소", "rotation", ["A", "B", "C"], "weekly"), ("논문", "milestone", ["A", "B"], "weekly")]
    assert sorted((t.date, t.assignee, t.done) for t in again) == sorted((t.date, t.assignee, t.done) for t in tasks)
    assert again[0].title in ("실험실 청소", "초고")


def test_work_tab_dashboard_and_calendar(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from app.core import settings
    from app.core.project import Project
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "_settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(settings, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(settings, "remember_project", lambda *_: None)
    from app.ui.main_window import MainWindow
    proj = Project.create(tmp_path / "t.labproj", "t")
    win = MainWindow(proj)
    ctrl = win.work
    p = w.WorkProject(name="청소", members=["A", "B", "C"])
    ctrl.upsert_project(p)
    ctrl.fill(p.id, "B", date(2026, 11, 2), (2026, 2), "weekly")
    assert (tmp_path / "업무.xlsx").exists()
    win.open_tab("work")
    assert win.stack.currentWidget() is win.work_page and win.work_page.pid == p.id
    assert win.work_page.people.names == ["A", "B", "C"]
    # dashboard box: this turn B, done moves it to C (struck-through row stays as the record)
    sec = win.dashboard.work_section
    assert [sec.table.item(0, c).text() for c in (0, 1, 3)] == ["청소", "2. B", "3. C"]
    sec.table.cellClicked.emit(0, 1)  # the blue person = done
    app.processEvents()
    # done: B's cell is struck through and the highlight moves to the next person (C)
    assert sec.table.item(0, 1).font().strikeOut() and sec.table.item(0, 3).text() == "3. C"
    assert sec.table.item(0, 3).font().weight() == 600
    assert sec.undo_btn.isEnabled() and sec.table.columnCount() == 5
    # drag-and-drop paths (the signals the drop handlers emit)
    win.work_page.people.reordered.emit(["C", "A", "B"])
    assert ctrl.project(p.id).members == ["C", "A", "B"]
    first_open = w.current_next(ctrl.tasks(), p.id)[0]
    win.work_page.month.task_moved.emit(first_open.id, date(2026, 11, 10))
    win.work_page.month.task_assigned.emit(first_open.id, "A")
    t = ctrl.task(first_open.id)
    assert (t.date, t.assignee) == ("2026-11-10", "A")
    ctrl.undo()
    assert ctrl.task(first_open.id).assignee == "C"
    # the 일정 calendar shows them as the 업무 calendar and a drag there moves the task
    evs = [e for e in win.schedule_page.visible_events() if e.calendar_id == "work"]
    assert len(evs) == 9 and evs[0].all_day
    from datetime import datetime
    win.schedule_page.move_event(evs[1], datetime(2026, 11, 20, 0, 0), datetime(2026, 11, 21, 0, 0))
    assert ctrl.task(evs[1].extra["work_task"]).date == "2026-11-20"
    win.close()
    proj.close()


def test_rotation_period_by_year_and_half(tmp_path):
    p = _proj()
    p.until = "2027-1"
    tasks = w.fill_rotation(p, [], "A", date(2026, 11, 2))  # weekly through 2027-06-30
    days = [t.day for t in tasks]
    assert days[0] == date(2026, 11, 2) and days[-1] <= date(2027, 6, 30) < days[-1] + timedelta(weeks=1)
    assert w.parse_half("2026년 하반기") == (2026, 2) and w.parse_half("2027-1") == (2027, 1)
    assert w.half_end(2026, 1) == date(2026, 6, 30)
    # unset period = the start's own half; saved as "2027년 상반기" and read back
    assert _proj().until_half == w.half_of(date.fromisoformat(_proj().start))
    path = tmp_path / "업무.xlsx"
    w.write_work(path, [p], tasks)
    ps, _ = w.read_work(path)
    assert ps[0].until_half == (2027, 1)
