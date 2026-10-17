# Glide (swipe) decoder static library on the vendored LatinIME sources.
# No GTK/glib here; plain C API in xxglide.h. Pulled into the Stevia build
# with subdir('glide'), guarded by the 'glide' option.

# The rest of Stevia is C; the decoder needs C++ only when glide is on.
add_languages('cpp', required : true)

javac = find_program('javac', required : true)
jdk_bin_dir = run_command('dirname', javac.full_path(), check : true).stdout().strip()
jdk_home = run_command('dirname', jdk_bin_dir, check : true).stdout().strip()
# javac is usually a symlink (alternatives); resolve to the real JDK home.
jdk_real = run_command('readlink', '-f', javac.full_path(), check : true).stdout().strip()
jdk_home = run_command('dirname', run_command('dirname', jdk_real, check : true).stdout().strip(),
  check : true).stdout().strip()
if host_machine.system() == 'darwin'
  jni_platform = 'darwin'
elif host_machine.system() == 'windows'
  jni_platform = 'win32'
else
  jni_platform = 'linux'
endif

xxglide_inc = include_directories(
  'latinime',
  jdk_home / 'include',
  jdk_home / 'include' / jni_platform,
)

# Same selection as tools/glide-replay's Makefile: every .cpp under latinime/.
latinime_srcs = run_command(
  'find', meson.current_source_dir() / 'latinime', '-name', '*.cpp',
  check : true,
).stdout().strip().split('\n')

xxglide_lib = static_library(
  'xxglide',
  'xxglide.cpp',
  latinime_srcs,
  include_directories : xxglide_inc,
  c_args : '-DHOST_TOOL',
  cpp_args : ['-DHOST_TOOL', '-Wno-unused-parameter', '-Wno-unused-but-set-variable'],
  override_options : 'cpp_std=c++17',
  install : false,
)

xxglide_dep = declare_dependency(
  link_with : xxglide_lib,
  include_directories : include_directories('.'),
)

# The UI lane finds the dictionary at <datadir>/xx-keyboard/dicts/.
install_data(
  'dicts/main_en-US.dict',
  'dicts/main_en-GB.dict',
  install_dir : get_option('datadir') / 'xx-keyboard' / 'dicts',
)

if get_option('tests')
  glide_test_defs = [
    '-DXXGLIDE_TEST_DICT="' + meson.current_source_dir() / 'dicts/main_en-US.dict"',
    '-DXXGLIDE_TEST_CORPUS="' + meson.current_source_dir() / 'test/corpus.jsonl"',
    '-DXXGLIDE_TEST_WORDS="' + meson.current_source_dir() / 'test/words.txt"',
  ]
  # Reference replay rate on the vendored corpus is top-1 16/19, top-4 16/19
  # (real tester glides; the 3 misses are genuine decoder misses, also missed
  # on device). The floor pins regressions, not the exact rate.
  glide_corpus_defs = glide_test_defs + [
    '-DXXGLIDE_CORPUS_TOP1_FLOOR=15',
    '-DXXGLIDE_CORPUS_TOP4_FLOOR=16',
  ]
  glide_decode_test = executable(
    'test-glide-decode',
    'test/test_glide_decode.cpp',
    dependencies : xxglide_dep,
    include_directories : xxglide_inc,
    c_args : glide_test_defs,
    cpp_args : glide_test_defs + ['-DHOST_TOOL'],
    override_options : 'cpp_std=c++17',
  )
  test('test-glide-decode', glide_decode_test, timeout : 300)
  glide_corpus_test = executable(
    'test-glide-corpus',
    'test/test_glide_corpus.cpp',
    dependencies : xxglide_dep,
    include_directories : xxglide_inc,
    c_args : glide_corpus_defs,
    cpp_args : glide_corpus_defs + ['-DHOST_TOOL'],
    override_options : 'cpp_std=c++17',
  )
  test('test-glide-corpus', glide_corpus_test, timeout : 300)
endif
