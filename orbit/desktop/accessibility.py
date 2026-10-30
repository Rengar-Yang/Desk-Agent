"""Read path bars and visible names through the accessibility bus."""

import re

from orbit.desktop.files import PLACE_NAMES


def dbus_result(connection, destination, path, interface, method, signature="", body=()):
    from jeepney import DBusAddress, MessageType, new_method_call
    message = new_method_call(DBusAddress(path, destination, interface), method, signature, body)
    reply = connection.send_and_get_reply(message, timeout=1.2)
    if reply.header.message_type == MessageType.error:
        raise RuntimeError(reply.body[0] if reply.body else "dbus error")
    return reply.body[0] if reply.body else None


def a11y_connection():
    from jeepney.io.blocking import open_dbus_connection
    session = open_dbus_connection(bus="SESSION")
    try:
        address = dbus_result(session, "org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress")
    finally:
        session.close()
    return open_dbus_connection(bus=address.split(",guid=")[0])


def a11y_properties(connection, destination, path):
    data = dbus_result(connection, destination, path, "org.freedesktop.DBus.Properties", "GetAll", "s", ("org.a11y.atspi.Accessible",))
    return {key: (value[1] if isinstance(value, tuple) and len(value) == 2 else value) for key, value in data.items()}


def accessibility_snapshot(pid, title, rect):
    """Read the path bar, visible names, and address text exposed by the window."""
    if not pid:
        return {}
    try:
        connection = a11y_connection()
    except Exception:
        return {}
    try:
        names = dbus_result(connection, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames")
        destination = None
        for name in names:
            if not str(name).startswith(":"):
                continue
            try:
                owner = dbus_result(connection, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", (name,))
            except Exception:
                continue
            if int(owner) == int(pid):
                destination = name
                break
        if not destination:
            return {}
        frames = dbus_result(connection, destination, "/org/a11y/atspi/accessible/root", "org.a11y.atspi.Accessible", "GetChildren")
        best = None
        best_distance = None
        for child_dest, child_path in frames:
            props = a11y_properties(connection, child_dest, child_path)
            role = dbus_result(connection, child_dest, child_path, "org.a11y.atspi.Accessible", "GetRoleName")
            if role not in ("frame", "window") or props.get("Name") != title:
                continue
            distance = 0
            try:
                extents = dbus_result(connection, child_dest, child_path, "org.a11y.atspi.Component", "GetExtents", "u", (0,))
                cx, cy = extents[0] + extents[2] / 2, extents[1] + extents[3] / 2
                distance = abs(cx - (rect[0] + rect[2] / 2)) + abs(cy - (rect[1] + rect[3] / 2))
            except Exception:
                distance = 0
            if best_distance is None or distance < best_distance:
                best = (child_dest, child_path)
                best_distance = distance
        if not best:
            return {}
        return walk_accessible(connection, *best)
    except Exception:
        return {}
    finally:
        connection.close()


def walk_accessible(connection, destination, root_path):
    path_buttons = []
    files = []
    selection = None
    blobs = []
    stack = [(destination, root_path, 0)]
    seen = 0
    while stack and seen < 420:
        dest, path, depth = stack.pop()
        seen += 1
        try:
            props = a11y_properties(connection, dest, path)
            role = dbus_result(connection, dest, path, "org.a11y.atspi.Accessible", "GetRoleName")
            children = dbus_result(connection, dest, path, "org.a11y.atspi.Accessible", "GetChildren") if props.get("ChildCount") else []
        except Exception:
            continue
        names = []
        child_nodes = []
        for child_dest, child_path in children:
            try:
                child_props = a11y_properties(connection, child_dest, child_path)
                child_role = dbus_result(connection, child_dest, child_path, "org.a11y.atspi.Accessible", "GetRoleName")
            except Exception:
                continue
            if child_role in ("menu", "menu item", "check menu item", "separator"):
                continue
            child_name = child_props.get("Name") or ""
            names.append((child_role, child_name))
            child_nodes.append((child_dest, child_path, child_role, child_name))
            if depth < 11:
                stack.append((child_dest, child_path, depth + 1))
        buttons = path_from_names(names)
        if len(buttons) > len(path_buttons):
            path_buttons = buttons
        segments = path_bar_segments(connection, child_nodes)
        if len(segments) > len(path_buttons):
            path_buttons = segments
        name = props.get("Name") or ""
        if role == "canvas" and name:
            files.append(name)
        if role in ("entry", "document", "link") and name:
            blobs.append(name)
        selected = re.search(r"[“\"](.+?)[”\"] selected|[“\"](.+?)[””]已选", name)
        if selected:
            selection = next(group for group in selected.groups() if group)
    url = next((item for item in blobs if item.startswith(("http://", "https://"))), None)
    return {"buttons": path_buttons, "files": files[:80], "selection": selection, "url": url}


def named_button(connection, destination, path):
    try:
        props = a11y_properties(connection, destination, path)
        if not props.get("ChildCount"):
            return None
        children = dbus_result(connection, destination, path, "org.a11y.atspi.Accessible", "GetChildren")
    except Exception:
        return None
    found = None
    for child_dest, child_path in children:
        try:
            child_props = a11y_properties(connection, child_dest, child_path)
            child_role = dbus_result(connection, child_dest, child_path, "org.a11y.atspi.Accessible", "GetRoleName")
        except Exception:
            continue
        child_name = child_props.get("Name") or ""
        if child_role == "push button" and child_name and child_name.lower() != "menu":
            found = child_name
    return found


def path_bar_segments(connection, child_nodes):
    """Read a path bar whose crumbs are sibling fillers, as in Nautilus."""
    if sum(1 for node in child_nodes if node[2] == "filler") < 2:
        return []
    segments = []
    started = False
    for dest, path, role, _name in child_nodes:
        if role != "filler":
            if started:
                break
            continue
        button = named_button(connection, dest, path)
        if not button:
            if started:
                break
            continue
        started = True
        segments.append(button)
    return segments if len(segments) >= 2 else []


def path_from_names(names):
    best = []
    for index, (role, name) in enumerate(names):
        if role != "push button" or not name or name.lower() == "menu":
            continue
        buttons = [name]
        cursor = index + 1
        while cursor + 1 < len(names) and names[cursor] == ("label", "/") and names[cursor + 1][0] == "push button" and names[cursor + 1][1]:
            buttons.append(names[cursor + 1][1])
            cursor += 2
        if len(buttons) > len(best):
            best = buttons
    if len(best) >= 2 or (len(best) == 1 and best[0].lower() in PLACE_NAMES):
        return best
    return []
