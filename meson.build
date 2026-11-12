project(
  'mizuki',
  version: '0.1.0',
  meson_version: '>=0.61.0',
  license: 'MIT',
)

python = import('python').find_installation('python3', required: true)
prefix = get_option('prefix')
datadir = get_option('datadir')
pkgdatadir = join_paths(prefix, datadir, 'mizuki')

# Python package lives in <prefix>/share/mizuki/mizuki so it can be imported
# with a single PYTHONPATH entry by the launcher below.
install_subdir(
  'src/mizuki',
  install_dir: join_paths(datadir, 'mizuki'),
  exclude_directories: ['__pycache__', '.mypy_cache'],
)

install_data('data/style.css', install_dir: pkgdatadir)
# Icons go into the standard hicolor theme so the shell and app launcher can
# resolve them, while dev runs add data/icons via Gtk.IconTheme search paths.
install_subdir(
  'data/icons',
  install_dir: datadir,
  exclude_directories: ['__pycache__'],
)

install_data(
  'data/io.github.hughlyell.Mizuki.desktop',
  install_dir: join_paths(datadir, 'applications'),
)

install_data(
  'data/io.github.hughlyell.Mizuki.metainfo.xml',
  install_dir: join_paths(datadir, 'metainfo'),
)

launcher_conf = configuration_data()
launcher_conf.set('PYTHON', python.full_path())
launcher_conf.set('pkgdatadir', pkgdatadir)
configure_file(
  input: 'bin/mizuki.in',
  output: 'mizuki',
  configuration: launcher_conf,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

test(
  'unit-tests',
  python,
  args: [
    '-m', 'unittest', 'discover',
    '-s', join_paths(meson.project_source_root(), 'tests'),
    '-v',
  ],
  env: ['PYTHONPATH=' + join_paths(meson.project_source_root(), 'src')],
  timeout: 60,
)

summary({
  'Python': python.full_path(),
  'Data dir': pkgdatadir,
}, section: 'Mizuki')
