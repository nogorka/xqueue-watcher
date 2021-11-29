"""
Implementation of a grader compatible with XServer
"""
from __future__ import absolute_import
from __future__ import unicode_literals
import imp
import sys
import cgi
import time
import json

import requests
from path import Path
import logging
import multiprocessing
from statsd import statsd
from skimage.metrics import structural_similarity as compare_ssim
import cv2
from tempfile import NamedTemporaryFile


def format_errors(errors):
    esc = cgi.escape
    error_string = ''
    error_list = [esc(e) for e in errors or []]
    if error_list:
        items = '\n'.join(['<li><pre>{0}</pre></li>\n'.format(e) for e in error_list])
        error_string = '<ul>\n{0}</ul>\n'.format(items)
        error_string = '<div class="result-errors">{0}</div>'.format(error_string)
    return error_string


def to_dict(result):
    # long description may or may not be provided.  If not, don't display it.
    # TODO: replace with mako template
    esc = cgi.escape
    if result[1]:
        long_desc = '<p>{0}</p>'.format(esc(result[1]))
    else:
        long_desc = ''
    return {'short-description': esc(result[0]),
            'long-description': long_desc,
            'correct': result[2],  # Boolean; don't escape.
            'expected-output': esc(result[3]),
            'actual-output': esc(result[4])
            }


def get_user_file(file_url):
    req = requests.get(file_url, stream=True)
    fd = NamedTemporaryFile()
    for chunk in req.iter_content():
        fd.write(chunk)
    fd.seek(0)
    return fd


def check_render(filename):
    original_path = 'img/001.png'

    original_img = cv2.imread(original_path)
    student_img = cv2.imread(filename)

    if original_img is not None:
        if student_img is not None:
            original_gray = cv2.cvtColor(original_img, cv2.COLOR_BGR2GRAY)
            student_gray = cv2.cvtColor(student_img, cv2.COLOR_BGR2GRAY)
            return compare_ssim(original_gray, student_gray, full=True)[0] * 100

    return -1


class Grader(object):
    results_template = """
<div class="test">
<header>Test results</header>
  <section>
    <div class="shortform">
    {status}
    </div>
    <div class="longform">
      {errors}
      {results}
    </div>
  </section>
</div>
"""

    results_correct_template = """
  <div class="result-output result-correct">
    <h4>{short-description}</h4>
    <pre>{long-description}</pre>
    <dl>
    <dt>Output:</dt>
    <dd class="result-actual-output">
       <pre>{actual-output}</pre>
       </dd>
    </dl>
  </div>
"""

    results_incorrect_template = """
  <div class="result-output result-incorrect">
    <h4>{short-description}</h4>
    <pre>{long-description}</pre>
    <dl>
    <dt>Your output:</dt>
    <dd class="result-actual-output"><pre>{actual-output}</pre></dd>
    <dt>Correct output:</dt>
    <dd><pre>{expected-output}</pre></dd>
    </dl>
  </div>
"""

    def __init__(self, grader_root='/tmp/', fork_per_item=True, logger_name=__name__):
        """
        grader_root = root path to graders
        fork_per_item = fork a process for every request
        logger_name = name of logger
        """
        self.log = logging.getLogger(logger_name)
        self.grader_root = Path(grader_root)

        self.fork_per_item = fork_per_item

    def __call__(self, content):
        if self.fork_per_item:
            q = multiprocessing.Queue()
            proc = multiprocessing.Process(target=self.process_item, args=(content, q))
            proc.start()
            proc.join()
            reply = q.get_nowait()
            if isinstance(reply, Exception):
                raise reply
            else:
                return reply
        else:
            return self.process_item(content)

    filename = 'render.png'

    def grade(self, grader_path, grader_config, files):
        score = 0
        files_json = json.loads(files)
        if self.filename in files_json:
            path = files_json[self.filename]

            fd = get_user_file(path)
            filename = fd.name

            score = check_render(filename)

            fd.close()

        wrong_result = {
            'score': 0,
            'msg': "Something is incorrect, try again!",
        }
        correct_result = {
            'score': 1,
            'msg': "Good job!",
        }
        server_work = {
            'score': 0,
            'msg': "Something is incorrect at the server side, connect to administrator",
        }
        if score == -1:
            return server_work
        if score > 95:
            return correct_result
        else:
            return wrong_result

    def process_item(self, content, queue=None):
        try:
            statsd.increment('xqueuewatcher.process-item')
            body = content['xqueue_body']
            files = content['xqueue_files']

            # Delivery from the lms
            body = json.loads(body)
            student_response = body['student_response']
            payload = body['grader_payload']
            try:
                grader_config = json.loads(payload)
            except ValueError as err:
                # If parsing json fails, erroring is fine--something is wrong in the content.
                # However, for debugging, still want to see what the problem is
                statsd.increment('xqueuewatcher.grader_payload_error')

                self.log.debug("error parsing: '{0}' -- {1}".format(payload, err))
                raise

            self.log.debug("Processing submission, grader payload: {0}".format(payload))
            relative_grader_path = ''
            grader_path = (self.grader_root / relative_grader_path).abspath()
            start = time.time()
            results = self.grade(grader_path, grader_config, files)

            statsd.histogram('xqueuewatcher.grading-time', time.time() - start)

            # Make valid JSON message
            reply = {
                'score': results['score'],
                'msg': results['msg'],
            }

            statsd.increment('xqueuewatcher.replies (non-exception)')
        except Exception as e:
            self.log.exception("process_item")
            if queue:
                queue.put(e)
            else:
                raise
        else:
            if queue:
                queue.put(reply)
            return reply

    def render_results(self, results):
        output = []
        test_results = [to_dict(r) for r in results['tests']]
        for result in test_results:
            if result['correct']:
                template = self.results_correct_template
            else:
                template = self.results_incorrect_template
            output += template.format(**result)

        errors = format_errors(results['errors'])

        status = 'INCORRECT'
        if errors:
            status = 'ERROR'
        elif results['correct']:
            status = 'CORRECT'

        return self.results_template.format(status=status,
                                            errors=errors,
                                            results=''.join(output))
