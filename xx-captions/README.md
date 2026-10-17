# xx-captions

Local live captions and call transcription for XX-WM phones. See PORT_SPEC.md.

Build and install `common/` first: the overlay (`xx-captions --overlay`)
and the settings window (`xx-captions --settings`) run on the shared
`xx_ui` design system (`common/xx_ui`), which `common/meson.build`
installs as the `xx_ui` python package. Configuring this app without it
fails with `xx_ui is not installed`.
