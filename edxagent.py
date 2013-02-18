"""
EdX Agent

The EdX Agent allows you to integrate a custom grader with the edX site. Most
parameters can be set in the config file, but can also be overridden via command
line options. See http://edx.github.com/edxagent for more info. This is my first
stab at a commandline interface, but I haven't actually implemented any of it.

Usage:
  edxagent.py [--config=FILE]
              [--daemon [--pid=FILE]] [--workers=N]
              [--post_url=URL]
              [--debug_logging] [--access_log=FILE] [--error_log=FILE]

  edxagent.py -h | --help
  edxagent.py --version

Options:
  -d --daemon          Run as daemon
  -w=N --workers=N     Number of simultaneous requests we allow this agent to 
                       initiate.
  --access_log=FILE    Where to write
  --debug_logging

  -h --help     Show this screen.
  --version     Show version.

================================================================================
TODO List

For internal use:
  * Read from config files, and switch handlers based on them.
  * Figure out what GraderRequest and GraderResult should have in them
  * Finish the other leg of this and push the response back to another queue
  * Create something that reads from that other queue and updates LMS db
  * Actually implement the commandline interface
  * Move logger setup
  * Create the on_decode_error for our GradeRequestConsumer
  * Implement command line Grader
  * Use optional credentials for RabbitMQ
  * Better debug tools
  * Datadog integration
  * QoS with multiple queues.
  * Daemon threads can cause problems:
      http://joeshaw.org/2009/02/24/605/
    So we should switch to a more manual accounting:
      http://www.regexprn.com/2010/05/killing-multithreaded-python-programs.html
      http://notemagnet.blogspot.com/2009/10/writing-monitoring-threads-in-python.html

For external use:
  * LMS query step where it has an API key and asks us what the RabbitMQ config
    should be. Not necessary if everything's in house.
  * Use daemon lib
"""
from datetime import datetime
from threading import Thread
import logging
import sys
import time

from docopt import docopt
from kombu import Connection, Exchange, Queue
from kombu.exceptions import MessageStateError
from kombu.mixins import ConsumerMixin

from graders import GraderRequest, HTTPGrader, SleepGrader

log = logging.getLogger("edxagent")
log.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter("%(asctime)s %(levelname)s %(process)d %(threadName)s " +
                              "[%(name)s] - %(message)s")
handler.setFormatter(formatter)
log.addHandler(handler)


def main():
    args = docopt(__doc__.partition("=========")[0], version="EdX Agent 0.1")

    # Do startup debug log stuff here. -- connection, user, log file locations
    # selected grader
    with Connection('amqp://guest:guest@localhost:5672//') as connection:
        grade_request_consumers = [GradeRequestConsumer(connection,
                                                        HTTPGrader("http://localhost/"))
                                   for _ in range(10)]
        threads = [Thread(target=grc.run, name="Worker {0}".format(i))
                   for i, grc in enumerate(grade_request_consumers)]
        for thread in threads:
            thread.setDaemon(True) # TODO: More graceful thread killing
            thread.start()

        while True:
            try:
                time.sleep(1)
            except KeyboardInterrupt:
                print("bye!")
                sys.exit(0)


class GradeRequestConsumer(ConsumerMixin):

    def __init__(self, connection, grader):
        """

        :param connection: A Kombu Connection object for our RabbitMQ connection
        :param grader: A subclass of graders.Grader
        """
        self.connection = connection
        self.grader = grader

        grading_exchange = Exchange("grading")
        self.queues = [Queue("agenttest",
                             exchange=grading_exchange,
                             routing_key="agenttest")]


    def get_consumers(self, Consumer, channel):
        """This is mostly boilerplate from Kombu's docs, as this is used by
        Kombu's ConsumerMixin that we inherit from.
        """
        # Note that we can have multiple callbacks called in sequence.
        consumers = [Consumer(self.queues, callbacks=[self.on_message])]

        # Force RabbitMQ to only send us a new message after we've ack'd the
        # last one. If this isn't set, Rabbit will push to consumers as quickly
        # as possible, which would give us uneven load when grader requests vary
        # widely in expense (e.g. programming problems).
        for consumer in consumers:
            consumer.qos(prefetch_count=1)

        return consumers

    def on_message(self, body, message):

        started_at = datetime.now()
        response = self.grader.grade(GraderRequest(data=body))
        time_elapsed = datetime.now() - started_at
        log.info(u"graded in %s: %s, %s", time_elapsed, body, response.data)

        # TODO: At this point, we'd have a producer and spit the reply back.

        # We're done, ack message to remove it from the queue. RabbitMQ knows
        # what consumer got this message, and knows whether we're still alive,
        # so it doesn't matter how long this takes to process.
        try:
            message.ack()
        except MessageStateError as state_err:
            # This can fire off if it's already been ack'd by a different worker
            log.error(state_err)


if __name__ == '__main__':
    main()
