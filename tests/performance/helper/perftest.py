import time
import numpy
import commands

from datetime import date
from mpi4py import MPI


def dump_timings(f):
    """
    Simple decorator measuring min/avg/max time of the inner function.
    """
    def _inner(*args, **kwargs):

        #TODO: is there a better way to get the class name?
        test_class_name = "%s" % (args[0])
        test_class_name = test_class_name.split('.')[1].split(')')[0]
        test_class_name = test_class_name.split(' ')[0]

        function_name = getattr(f, "__name__", "<unnamed>")
        if test_class_name.find("test") != -1:
            function_name = function_name.split('test_')[1]

        mode = "a"
        #if size == 1:
            #mode = "w"

        #XXX: children have their own COMM_WORLD (master excluded)
        comm = MPI.COMM_WORLD
        rank = comm.Get_rank()
        size = comm.Get_size()

        t_start  = time.time()
        test_out = f(*args, **kwargs)
        t_end    = time.time()
        dt       = numpy.array([t_end - t_start])

        dt_min = numpy.zeros(1)
        dt_max = numpy.zeros(1)
        dt_sum = numpy.zeros(1)
        comm.Reduce([dt, MPI.DOUBLE], [dt_min, MPI.DOUBLE], op=MPI.MIN, root=0)
        comm.Reduce([dt, MPI.DOUBLE], [dt_max, MPI.DOUBLE], op=MPI.MAX, root=0)
        comm.Reduce([dt, MPI.DOUBLE], [dt_sum, MPI.DOUBLE], op=MPI.SUM, root=0)

        if rank is 0:
            average = dt_sum[0] / size
            print "%s took (%s / %s / %s)s" % (function_name, dt_min[0], average, dt_max[0])

            hostname = commands.getoutput("hostname")
            filename = "%s_%s_%s_%s.perf" % (hostname, test_class_name, function_name, date.today())
            measurements = "%s %s %s %s\n" % (size, dt_min[0], average, dt_max[0])
            with open(filename, mode) as out:
                out.write(measurements)

    return _inner


import inspect
class TestCase:

    def __init__(self):
        pass

    def tearDown(self):
        pass

    def setUp(self):
        pass

    def main(self):
        """
        Run all methods starting with 'test_' of this class using the
        communicator of the spawned MPI threads.
        The _test method should make use of the dump_timings decorator.
        """

        self.parent_comm = MPI.Comm.Get_parent()

        self.setUp()

        members = inspect.getmembers(self, predicate=inspect.ismethod)
        for potential_test in members:
            if potential_test[0].find('test_') == 0:
                potential_test[1]()

        self.parent_comm.Barrier()
        self.parent_comm.Disconnect()

        self.tearDown()

