from sanitize import sanitize_filename, unique_path


def test_traversal_and_separators():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("..\\..\\secret") == "secret"
    assert sanitize_filename("/abs/path.jpg") == "path.jpg"
    assert sanitize_filename("a/b\\c") == "c"


def test_hidden_and_dot_names():
    assert sanitize_filename(".profile") == "profile"
    assert sanitize_filename("...") == "file"
    assert sanitize_filename(".") == "file"
    assert sanitize_filename("..") == "file"
    assert sanitize_filename("") == "file"
    assert sanitize_filename(None) == "file"


def test_control_chars_and_whitespace():
    assert sanitize_filename("  photo\x00.jpg  ") == "photo.jpg"
    assert sanitize_filename("a\nb") == "ab"


def test_normal_names_kept():
    assert sanitize_filename("Grüße 2024 (1).HEIC") == "Grüße 2024 (1).HEIC"
    assert sanitize_filename("a" * 300 + ".txt").endswith(".txt")
    assert len(sanitize_filename("a" * 300 + ".txt")) == 255


def test_unique_path_collisions(tmp_path):
    first = unique_path(tmp_path, "photo.jpg")
    first.write_text("x")
    second = unique_path(tmp_path, "photo.jpg")
    assert second.name == "photo (1).jpg"
    second.write_text("x")
    assert unique_path(tmp_path, "photo.jpg").name == "photo (2).jpg"


def test_unique_path_no_extension(tmp_path):
    target = unique_path(tmp_path, "README")
    target.write_text("x")
    assert unique_path(tmp_path, "README").name == "README (1)"
