#!/usr/bin/env python3
from sys import argv, exc_info
from os import scandir, remove
from os.path import splitext, isfile, isdir, dirname
from subprocess import run, check_output, check_call, CalledProcessError
from xml.etree import ElementTree
from jinja2 import Template
from syslog import syslog, openlog, closelog, LOG_PID, LOG_USER, LOG_INFO, LOG_DEBUG, LOG_ERR
from tempfile import mkstemp

SCRIPT_NAME = 'ffmpeg-transcode'
SIZE_THRESHOLD = 512 * 1000 * 1000
OUT_FORMAT = 'mp4'


def try_find_input_path(data_path):
    input_paths = []
    for entry in scandir(data_path):
        if not entry.is_file():
            continue

        stat = entry.stat()
        if stat.st_size < SIZE_THRESHOLD:
            continue

        input_paths.append(entry.path)

    num_of_input_paths = len(input_paths)
    if num_of_input_paths == 0:
        syslog(LOG_ERR, 'No inputs found')
        exit(1)
    elif num_of_input_paths > 1:
        syslog(LOG_ERR, 'More than one input found: {}'.format(num_of_input_paths))
        exit(1)

    return input_paths[0]


openlog(SCRIPT_NAME, LOG_PID, LOG_USER)
try:
    data_path = argv[1]
    is_open = True if argv[2] == '1' else False
    is_active = True if argv[3] == '1' else False

    syslog(LOG_DEBUG, 'data_path: {}'.format(data_path))
    syslog(LOG_DEBUG, 'is_open: {}'.format(is_open))
    syslog(LOG_DEBUG, 'is_active: {}'.format(is_active))

    if isdir(data_path):
        input_path = try_find_input_path(data_path)
    elif isfile(data_path):
        input_path = data_path
    else:
        syslog(LOG_ERR, 'Data path must be a file or directory: {}'.format(data_path))
        exit(1)

    _, temp_path = mkstemp(dir=dirname(input_path))
    output_path = '{}-stereo.{}'.format(splitext(input_path)[0], OUT_FORMAT)

    syslog(LOG_DEBUG, 'Input path: {}'.format(input_path))
    syslog(LOG_DEBUG, 'Temp path: {}'.format(temp_path))
    syslog(LOG_DEBUG, 'Output path: {}'.format(output_path))

    try:
        output = check_output([
            'ffprobe',
            '-loglevel', 'quiet',
            '-print_format', 'xml',
            '-show_format',
            '-show_streams',
            input_path
        ])
    except CalledProcessError as ex:
        syslog(LOG_ERR, 'Failed to probe {}: {}'.format(input_path, ex))
        exit(1)

    xml = ElementTree.fromstring(output)

    audio_streams = xml.findall('.//stream[@codec_type="audio"]')
    stream_indexes = []
    for audio_stream in audio_streams:
        channels = audio_stream.attrib['channels']
        codec_name = audio_stream.attrib['codec_name']
        if channels <= "2" and codec_name == 'aac':
            continue

        stream_indexes.append(audio_stream.attrib['index'])

    syslog(LOG_DEBUG, 'Stream indexes: {}'.format(stream_indexes))

    num_of_stream_indexes = len(stream_indexes)
    if num_of_stream_indexes == 0:
        syslog(LOG_INFO, 'No streams found, nothing to do')
        try:
            remove(temp_path)
        except OSError:
            pass
        exit(0)

    task = Template('''\
logger --id=$$ --tag {{ script_name }} 'Starting: {{ input_path }} -> {{ temp_path }}'
ffmpeg \
-y \
-progress /tmp/{{ script_name }}-progress.log \
-i '{{ input_path }}' \
-codec:v copy \
-codec:s mov_text \
{% for stream_index in stream_indexes -%}
-codec:a:{{ stream_index }} aac \
{% endfor -%}
-af "pan=stereo|FL < 1.0*FL + 0.707*FC + 0.707*BL|FR < 1.0*FR + 0.707*FC + 0.707*BR" \
-map 0 \
-f {{ out_format }} \
'{{ temp_path }}' 2> /tmp/{{ script_name }}-error.log
logger --id=$$ --tag {{ script_name }} 'Finished: {{ input_path }} -> {{ temp_path }}'
logger --id=$$ --tag {{ script_name }} 'Trying: {{ temp_path }} -> {{ output_path }}'
logger --id=$$ --tag {{ script_name }} "Moving: `mv -v '{{ temp_path }}' '{{ output_path }}'`"
logger --id=$$ --tag {{ script_name }} 'Moved: {{ temp_path }} -> {{ output_path }}'
\
''').render(script_name=SCRIPT_NAME,
            input_path=input_path,
            temp_path=temp_path,
            output_path=output_path,
            stream_indexes=stream_indexes,
            out_format=OUT_FORMAT)

    syslog(LOG_DEBUG, 'Task: {}'.format(task))

    try:
        run(['at', '-M', 'now'], check=True, input=task.encode())
        syslog(LOG_DEBUG, 'Successfully scheduled task')
    except CalledProcessError as ex:
        syslog(LOG_ERR, 'Failed schedule task: {}'.format(ex))
        exit(1)
    finally:
        syslog(LOG_DEBUG, 'Script done')
except SystemExit as ex:
    syslog(LOG_DEBUG, 'Exit code: {}'.format(ex))
    raise ex
except:
    syslog(LOG_ERR, 'Unexpected error: {}'.format(exc_info()[0]))
    raise
finally:
    closelog()
