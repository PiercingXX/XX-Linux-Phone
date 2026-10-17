# ruff: noqa: F821 -- snap.py execs this with win/app in scope
view = win.dialer_view
view.show_page("people")
try:
    people = view.people
    store = people._store
    for i in range(store.get_n_items()):
        item = store.get_item(i)
        if item is not None and item.kind == "item":
            person = people.controller.find(item.uid)
            if person is not None:
                pop = people._open_sheet(person, people._list)
                if pop is not None:
                    pop.popdown()
                    pop.unparent()
            break
except Exception:
    pass
