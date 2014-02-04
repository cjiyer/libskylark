import errors
import ctypes
from ctypes import byref, cdll, c_double, c_void_p, c_int, c_char_p, pointer, POINTER, c_bool
import sprand
import math
from math import sqrt, pi
import numpy, scipy
import scipy.fftpack
import sys
import os
import time
import atexit

# Function for initialization and reinitilialization
def initialize(seed=-1):
  """
  Reinitalize the library with a seed. If seed is -1 then system time is
  used to create the seed.
  """

  global _lib, _ctxt_obj, _ELEM_INSTALLED, _KDT_INSTALLED
  global SUPPORTED_SKETCH_TRANSFORMS
  global _rank, _size

  if '_lib' not in globals():
    try:
      #
      # Load C-API library and set return types
      #
      _lib = cdll.LoadLibrary('libcskylark.so')
      _lib.sl_create_context.restype              = c_int
      _lib.sl_create_default_context.restype      = c_int
      _lib.sl_free_context.restype                = c_int
      _lib.sl_context_rank.restype                = c_int
      _lib.sl_context_size.restype                = c_int
      _lib.sl_create_sketch_transform.restype     = c_int
      _lib.sl_wrap_raw_matrix.restype             = c_int
      _lib.sl_free_raw_matrix_wrap.restype        = c_int
      _lib.sl_strerror.restype                    = c_char_p
      _lib.sl_supported_sketch_transforms.restype = c_char_p
      _lib.sl_has_elemental.restype               = c_bool
      _lib.sl_has_combblas.restype                = c_bool
      
      _ELEM_INSTALLED = _lib.sl_has_elemental()
      _KDT_INSTALLED  = _lib.sl_has_combblas()    
      
      csketches = map(eval, _lib.sl_supported_sketch_transforms().split())
      pysketches = ["FastGaussianRFT", "SJLT", "PPT", "URST", "NURST"]
      SUPPORTED_SKETCH_TRANSFORMS = \
          csketches + [ (T, "Matrix", "Matrix") for T in pysketches]
    except:
      # Did not find library -- must rely on Python code
      _lib = None
      _ELEM_INSTALLED = False
      _KDT_INSTALLED = False
      sketches = ["JLT", "CT", "SJLT", "FJLT", "CWT", "MMT", "WZT", "GaussianRFT", 
                  "FastGaussianRFT", "PPT", "URST", "NURST"]
      SUPPORTED_SKETCH_TRANSFORMS = [ (T, "Matrix", "Matrix") for T in sketches]

    # TODO reload dll ?

  if seed == -1:
    seed = int(time.time())

  if _lib is None:
    # We assume completly local operation when no C++ layer.
    _rank = 1
    _size = 1
    numpy.random.seed(seed)
    return
    
  if '_ctxt_obj' in globals():
    _lib.sl_free_context(_ctxt_obj)

  ctxt_obj = c_void_p()
  _lib.sl_create_default_context(seed, byref(ctxt_obj))
  _ctxt_obj = ctxt_obj.value

  rank = c_int()
  _lib.sl_context_rank(_ctxt_obj, byref(rank))
  _rank = rank.value

  size = c_int()
  _lib.sl_context_size(_ctxt_obj, byref(size))
  _size = size.value

  # TODO the following is temporary. Random numbers should be taken from library
  #      even for pure-Python implementations.
  if _lib is not None:
    numpy.random.seed(seed)

# Allow finalization
def finalize():
  """
  Finalize (de-allocate) the library. However, note that that will not cause
  allocated objects (e.g. sketch transforms) to be freed. They are freed by
  the garbage collector when detected as garbage (no references).
  """
  # TODO free dll (?)
  global _lib, _ctxt_obj, _rank, _size
  if _lib is not None:
    if _ctxt_obj != 0:
      _lib.sl_free_context(_ctxt_obj)
    _ctxt_obj = 0

# Make sure finalize is called before exiting (just in case).
atexit.register(finalize)

# Actually initialize the C-API.
initialize(int(time.time()))

def _callsl(f, *args):
  errno = f(*args)
  if errno != 0:
    raise errors.UnexpectedLowerLayerError(_lib.sl_strerror(errno))

#
# Matrix type adapters: specifies how to interact with the underlying (perhaps in C/C++)
# data structure.
#
class _NumpyAdapter:
  def __init__(self, A):
    self._A = A

  def ctype(self):
    return "Matrix"

  def ptr(self):
    data = c_void_p()
    # If the matrix is kept in C ordering we are essentially wrapping the transposed
    # matrix
    if self.getorder() == "F":
      _callsl(_lib.sl_wrap_raw_matrix, \
                self._A.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), \
                self._A.shape[0], self._A.shape[1] if self._A.ndim > 1 else 1 , byref(data))
    else:
      _callsl(_lib.sl_wrap_raw_matrix, \
                self._A.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), \
                self._A.shape[1] if self._A.ndim > 1 else self._A.shape[0], \
                self._A.shape[0] if self._A.ndim > 1 else 1 , \
                byref(data))
    self._ptr = data.value
    return data.value

  def ptrcleaner(self):
    _callsl(_lib.sl_free_raw_matrix_wrap, self._ptr);

  def getdim(self, dim):
    return self._A.shape[dim]

  def getobj(self):
    return self._A

  def getorder(self):
    if self._A.flags.f_contiguous:
      return 'F'
    else:
      return 'C'

  def iscompatible(self, B):
    if isinstance(B, _NumpyAdapter) and self.getorder() != B.getorder():
      return "sketching numpy array to numpy array requires same element ordering", None
    elif not isinstance(B, _NumpyAdapter) and self.getorder() == 'C':
      return "numpy combined with other types must have fortran ordering", None
    else:
      return None, self._A.flags.c_contiguous

  def getctor(self):
    return _NumpyAdapter.ctor

  @staticmethod
  def ctor(m, n, B):
    # Construct numpy array that is compatible with B. If B is a numpy array the
    # element order (Fortran or C) must match. For all others (e.g., Elemental 
    # and KDT) the order must be Fortran because this is what the lower layers 
    # expect.
    if isinstance(B, _NumpyAdapter):
      return numpy.empty((m,n), order=B.getorder())
    else:
      return numpy.empty((m,n), order='F')

if _ELEM_INSTALLED:
  class _ElemAdapter:
    def __init__(self, A):
      self._A = A
      if isinstance(A, elem.DistMatrix_d):
        self._typeid = ""
      elif isinstance(A, elem.DistMatrix_d_VC_STAR):
        self._typeid ="VC_STAR"
      elif isinstance(A, elem.DistMatrix_d_VR_STAR):
        self._typeid = "VR_STAR"
      elif isinstance(A, elem.DistMatrix_d_STAR_VC):
        self._typeid = "STAR_VC"
      elif isinstance(A, elem.DistMatrix_d_STAR_VR):
        self._typeid = "STAR_VR"
      else:
        raise errors.UnsupportedError("Unsupported Elemental type")
      self._typestr = "DistMatrix_" + self._typeid

    def ctype(self):
      return self._typestr

    def ptr(self):
      return ctypes.c_void_p(long(self._A.this))

    def ptrcleaner(self):
      pass

    def getdim(self, dim):
      if dim == 0:
        return self._A.Height
      if dim == 1:
        return self._A.Width

    def getobj(self):
      return self._A

    def iscompatible(self, B):
      if isinstance(B, _NumpyAdapter) and B.getorder() != 'F':
        return "numpy combined with other types must have fortran ordering", None
      else:
        return None, False

    def getctor(self):
      return lambda m, n, c : _ElemAdapter.ctor(self._typeid, m, n, c)
 
    @staticmethod
    def ctor(typeid, m, n, B):
      if typeid is "":
        cls = elem.DistMatrix_d
      else:
        cls = eval("elem.DistMatrix_d_" + typeid)
      return cls(m, n)


if _KDT_INSTALLED:
  class _KDTAdapter:
    def __init__(self, A):
      self._A = A

    def ctype(self):
      return "DistSparseMatrix"

    def ptr(self):
      return ctypes.c_void_p(long(self._A._m_.this))

    def ptrcleaner(self):
      pass

    def getdim(self, A, dim):
      if dim == 0:
        return self._A.nrow()
      if dim == 1:
        return self._A.ncol()

    def getobj(self):
      return self._A

    def iscompatible(self, B):
      if isinstance(B, _NumpyAdapter) and B.getorder() != 'F':
        return "numpy combined with other types must have fortran ordering", None
      else:
        return None, False

    def getctor(self):
      return _KDTAdapter.ctor

    @staticmethod
    def ctor(m, n, B):
      import kdt
      nullVec = kdt.Vec(0, sparse=False)
      return kdt.Mat(nullVec, nullVec, nullVec, n, m)

#
# The following functions adapts an object to a uniform interface, so 
# that we can have a uniform way of accessing it. 
#
def _adapt(obj):
  if _ELEM_INSTALLED and sys.modules.has_key('elem'):
    global elem
    import elem
    elemcls = [elem.DistMatrix_d,
               elem.DistMatrix_d_VR_STAR, elem.DistMatrix_d_VC_STAR, 
               elem.DistMatrix_d_STAR_VC, elem.DistMatrix_d_STAR_VR] 
  else:
    elemcls = []

  if _KDT_INSTALLED and sys.modules.has_key('kdt'):
    global kdt
    import kdt
    kdtcls = [kdt.Mat]
  else:
    kdtcls = [];

  if isinstance(obj, numpy.ndarray):
    return _NumpyAdapter(obj)

  elif any(isinstance(obj, c) for c in elemcls):
    return _ElemAdapter(obj)

  elif any(isinstance(obj, c) for c in kdtcls):
      return _KDTAdapter(obj)
  
  else:
    raise errors.InvalidObjectError("Invalid/unsupported object passed as A or SA")

#
# Create mapping between type string and and constructor for that type
#
_map_to_ctor = { }
_map_to_ctor["LocalMatrix"] = _NumpyAdapter.ctor

if _ELEM_INSTALLED:
  _map_to_ctor["DistMatrix"] = lambda m, n, c : _ElemAdapter.ctor("", m, n, c)
  _map_to_ctor["DistMatrix_VR_STAR"] = lambda m, n, c : _ElemAdapter.ctor("VR_STAR", m, n, c)
  _map_to_ctor["DistMatrix_VC_STAR"] = lambda m, n, c : _ElemAdapter.ctor("VC_STAR", m, n, c)
  _map_to_ctor["DistMatrix_STAR_VR"] = lambda m, n, c : _ElemAdapter.ctor("STAR_VC", m, n, c)
  _map_to_ctor["DistMatrix_STAR_VC"] = lambda m, n, c : _ElemAdapter.ctor("STAR_VR", m, n, c)

if _KDT_INSTALLED:
  _map_to_ctor["DistSparseMatrix"] = _KDTAdapter.ctor

#
# Generic Sketch Transform
#
class _SketchTransform(object):
  """
  A sketching transform - in very general terms - is a dimensionality-reducing map 
  from R^n to R^s which preserves key structural properties.

  _SketchTransform is base class sketch transforms. The various sketch transforms derive 
  from this class and as such it defines a common interface. Derived classes can have different 
  constructors. The class is not meant
  """

  def __init__(self, ttype, n, s, defouttype=None):
    """
    Create the transform from n dimensional vectors to s dimensional vectors. Here we define
    the interface, but the constructor should not be called directly by the user.

    :param ttype: String identifying the sketch type. This parameter is omitted 
                  in derived classes.
    :param n: Number of dimensions in input vectors.
    :param s: Number of dimensions in output vectors.
    :param defouttype: Default output type when using the * and / operators. 
                       If None the output will have same type as the input.
    :returns: the transform object
    """
    
    self._baseinit(ttype, n, s, defouttype)
    if not self._ppy:
      sketch_transform = c_void_p()
      _callsl(_lib.sl_create_sketch_transform, _ctxt_obj, ttype, n, s, byref(sketch_transform))
      self._obj = sketch_transform.value

  def _baseinit(self, ttype, n, s, defouttype):
    if defouttype is not None and not _map_to_ctor.has_key(defouttype):
      raise errors.UnsupportedError("Unsupported default output type (%s)" % defouttype)
    self._ttype = ttype
    self._n = n
    self._s = s
    self._defouttype = defouttype
    self._ppy = _lib is None

  def __del__(self):
    if not self._ppy:
      _callsl(_lib.sl_free_sketch_transform, self._obj)

  def apply(self, A, SA, dim=0):
    """
    Apply the transform on **A** along dimension **dim** and write
    result in **SA**. Note: for rowwise (aka right) sketching **A**
    is mapped to **A S^T**.

    :param A: Input matrix.
    :param SA: Ouptut matrix. If "None" then the output will be allocated.
    :param dim: Dimension to apply along. 0 - columnwise, 1 - rowwise.
                or can use "columnwise"/"rowwise", "left"/"right"
                default is columnwise
    :returns: SA
    """
    if dim == 0 or dim == "columnwise" or dim == "left":
      dim = 0
    if dim == "rowwise" or dim == "right":
      dim = 1
    if dim != 0 and dim != 1:
      raise ValueError("Dimension must be either columnwise/rowwise or left/right or 0/1")

    A = _adapt(A)

    # Allocate in case SA is not given, and then adapt it.
    if SA is None:
      if self._defouttype is None:
        ctor = A.getctor()
      else:
        ctor = _map_to_ctor[self._defouttype]

      if dim == 0:
        SA = ctor(self._s, A.getdim(1), A)
      if dim == 1:
        SA = ctor(A.getdim(0), self._s, A)
    SA = _adapt(SA)

    reqcomb = (self._ttype, A.ctype(), SA.ctype())
    if reqcomb not in SUPPORTED_SKETCH_TRANSFORMS:
      raise errors.UnsupportedError("Unsupported transform-input-output combination: " \
                                      + str(reqcomb))  

    incomp, cinvert = A.iscompatible(SA)
    if incomp is not None:
      raise errors.UnsupportedError("Input and output are incompatible: " + incomp)

    if A.getdim(dim) != self._n:
      raise errors.DimensionMistmatchError("Sketched dimension is incorrect (input)")
    if SA.getdim(dim) != self._s:
      raise errors.DimensionMistmatchError("Sketched dimension is incorrect (output)")
    if A.getdim(1 - dim) != SA.getdim(1 - dim):
      raise errors.DimensionMistmatchError("Sketched dimension is incorrect (input != output)")

    if self._ppy:
      self._ppyapply(A.getobj(), SA.getobj(), dim)
    else:
      Aobj = A.ptr()
      SAobj = SA.ptr()
      if (Aobj == -1 or SAobj == -1):
        raise errors.InvalidObjectError("Invalid/unsupported object passed as A or SA")

      if cinvert:
        cdim = 1 - dim
      else:
        cdim = dim

      _callsl(_lib.sl_apply_sketch_transform, self._obj, \
                A.ctype(), Aobj, SA.ctype(), SAobj, cdim+1) 

      A.ptrcleaner()
      SA.ptrcleaner()

    return SA.getobj()

  def __mul__(self, A):
    """
    Allocate space for **SA** and apply the transform columnwise to **A**
    writing the result to **SA** and returning it.

    :param A: Input matrix.
    :returns: the result of applying the transform to **A** columnwise.
    """
    return self.apply(A, None, dim=0)

  def __div__(self, A):
    """
    Allocate space for **SA** and apply the transform rowwise to **A**
    writing the result to **SA** and returning it.

    :param A: Input matrix.
    :returns: the result of applying the transform to **A** rowwise.
    """
    return self.apply(A, None, dim=1)

  def getindim(self):
    """
    Get size of input.
    """
    return self._n

  def getsketchdim(self):
    """
    Get dimension of sketched output.
    """
    return self._s

#
# Various sketch transforms
#

class JLT(_SketchTransform):
  """
  The classic Johnson-Lindenstrauss dense sketching using Gaussian Random maps. 

  :param n: Number of dimensions in input vectors.
  :param s: Number of dimensions in output vectors.
  :param defouttype: Default output type when using the * and / operators.  

  Examples
  --------
  Let us bring *skylark* and other relevant Python packages into our environment.
  Here we demonstrate a non-distributed usage implemented using numpy arrays.  
  See section on working with distributed dense and sparse matrices.

  >>> import skylark, skylark.utilities, skylark.sketch
  >>> import scipy
  >>> import numpy.random
  >>> import matplotlib.pyplot as plt
    
  Let us generate some data, e.g., a data matrix whose entries are sampled 
  uniformly from the interval [-1, +1].
    
  >>> n = 300
  >>> d = 1000
  >>> A = numpy.random.uniform(-1.0,1.0, (n,d))
    
  Create a sketch operator corresponding to JLT sketching from d = 1000 
  to s = 100.
    
  >>> s = 100 
  >>> S = skylark.sketch.JLT(d, s)
    
  Let us sketch A row-wise:
    
  >>> B = S / A
    
  Let us compute norms of the row-vectors before and after sketching.
    
  >>> norms_A = skylark.utilities.norms(A)
  >>> norms_B = skylark.utilities.norms(B)
    
  Plot the histogram of distortions (ratio of norms for original to sketched 
  vectors).

  >>> distortions = scipy.ravel(norms_A/norms_B)
  >>> plt.hist(distortions,10)
  >>> plt.show()   
  """
  def __init__(self, n, s, defouttype=None):
    super(JLT, self).__init__("JLT", n, s, defouttype);
    if self._ppy:
      # The following is not memory efficient, but for a pure Python impl it will do
      self._S = numpy.random.standard_normal((s, n)) / sqrt(s)

  def _ppyapply(self, A, SA, dim):
    if dim == 0:
      SA1 = numpy.dot(self._S, A)
    if dim == 1:
      SA1 = numpy.dot(A, self._S.T)

    # We really want to use the out parameter of numpy.dot, but it does not seem 
    # to work (raises a ValueError)
    numpy.copyto(SA, SA1)

class SJLT(_SketchTransform):
  """
  Sparse Johnson-Lindenstrauss Transform

  Alternative name: SparseJLT

  :param n: Number of dimensions in input vectors.
  :param s: Number of dimensions in output vectors.
  :param density: Density of the transform matrix. Lower density require higher s.
  :param defouttype: Default output type when using the * and / operators.

  *D. Achlipotas*, **Database-frinedly random projections: Johnson-Lindenstrauss
  with binary coins**, Journal of Computer and System Sciences 66 (2003) 671-687

  *P. Li*, *T. Hastie* and *K. W. Church*, **Very Sparse Random Projections**,
  KDD 2006
  """
  def __init__(self, n, s, density = 1 / 3.0, defouttype=None):
    super(SJLT, self)._baseinit("SJLT", n, s, defouttype);
    self._ppy = True
    nz_values = [-sqrt(1.0/density), +sqrt(1.0/density)]
    nz_prob_dist = [0.5, 0.5]
    self._S = sprand.sample(s, n, density, nz_values, nz_prob_dist) / sqrt(s)  
    # QUESTION do we need to mulitply by sqrt(1/density) ???
    
  def _ppyapply(self, A, SA, dim):
    if dim == 0:
      SA1 = self._S * A
    if dim == 1:
      SA1 = A * self._S.T

    # We really want to use the out parameter of numpy.dot, but it does not seem 
    # to work (raises a ValueError)
    numpy.copyto(SA, SA1)

class CT(_SketchTransform):
  """
  Cauchy Transform

  :param n: Number of dimensions in input vectors.
  :param s: Number of dimensions in output vectors.
  :param C: Parameter trading embedding size and distortion. See paper for details.
  :param defouttype: Default output type when using the * and / operators.

  *C. Sohler* and *D. Woodruff*, **Subspace Embeddings for the L_1-norm with 
  Application**, STOC 2011
  """
  def __init__(self, n, s, C, defouttype=None):
    super(CT, self)._baseinit("CT", n, s, defouttype)

    if self._ppy:
      self._S = numpy.random.standard_cauchy((s, n)) * (C / s)
    else:
      sketch_transform = c_void_p()
      _callsl(_lib.sl_create_sketch_transform, _ctxt_obj, "CT", n, s, \
                byref(sketch_transform), ctypes.c_double(C))
      self._obj = sketch_transform.value

  def _ppyapply(self, A, SA, dim):
    if dim == 0:
      SA1 = numpy.dot(self._S, A)
    if dim == 1:
      SA1 = numpy.dot(A, self._S.T)

    # We really want to use the out parameter of numpy.dot, but it does not seem 
    # to work (raises a ValueError)
    numpy.copyto(SA, SA1)

class FJLT(_SketchTransform):
  """
  Fast Johnson-Lindenstrauss Transform

  Alternative class name: FastJLT

  :param n: Number of dimensions in input vectors.
  :param s: Number of dimensions in output vectors.
  :param defouttype: Default output type when using the * and / operators.

  *N. Ailon* and *B. Chazelle*, **The Fast Johnson-Lindenstrauss Transform and 
  Approximate Nearest Neighbors**, SIAM Journal on Computing 39 (1), pg. 302-322
  """
  def __init__(self, n, s, defouttype=None):
    super(FJLT, self).__init__("FJLT", n, s, defouttype);
    if self._ppy:
      d = scipy.stats.rv_discrete(values=([-1,1], [0.5,0.5]), name = 'uniform').rvs(size=n)
      self._D = scipy.sparse.spdiags(d, 0, n, n)
      self._S = URST(n, s, outtype)

  def _ppyapply(self, A, SA, dim):
    if dim == 0:
      DA = self._D * A
      FDA = scipy.fftpack.dct(DA, axis = 0, norm = 'ortho')
      self._S.apply(FDA, SA, dim);

    if dim == 1:
      AD = A * self._D
      ADF = scipy.fftpack.dct(AD, axis = 1, norm = 'ortho')
      self._S.apply(ADF, SA, dim);

class CWT(_SketchTransform):
  """
  Clarkson-Woodruff Transform (also known as CountSketch)

  Alternative class name: CountSketch
  
  :param n: Number of dimensions in input vectors.
  :param s: Number of dimensions in output vectors.
  :param defouttype: Default output type when using the * and / operators.
  
  *K. Clarkson* and *D. Woodruff*, **Low Rank Approximation and Regression
  in Input Sparsity Time**, STOC 2013
  """
  def __init__(self, n, s, defouttype=None):
    super(CWT, self).__init__("CWT", n, s, defouttype);
    if self._ppy:
      # The following is not memory efficient, but for a pure Python impl 
      # it will do
      distribution = scipy.stats.rv_discrete(values=([-1.0, +1.0], [0.5, 0.5]), 
                                             name = 'dist')
      self._S = sprand.hashmap(s, n, distribution, dimension = 0)

  def _ppyapply(self, A, SA, dim):
    if dim == 0:
      SA1 = self._S * A
    if dim == 1:
      SA1 = A * self._S.T

    # We really want to use the out parameter of scipy.dot, but it does not seem 
    # to work (raises a ValueError)
    numpy.copyto(SA, SA1)

class MMT(_SketchTransform):
  """
  Meng-Mahoney Transform. A variant of CountSketch (Clarkson-Woodruff Transform)
  using for low-distrition in the L1-norm.

  :param n: Number of dimensions in input vectors.
  :param s: Number of dimensions in output vectors.
  :param defouttype: Default output type when using the * and / operators.

  *X. Meng* and *M. W. Mahoney*, **Low-distortion Subspace Embeddings in
  Input-sparsity Time and Applications to Robust Linear Regression**, STOC 2013
  """
  def __init__(self, n, s, defouttype=None):
    super(MMT, self).__init__("MMT", n, s, defouttype);
    if self._ppy:
      # The following is not memory efficient, but for a pure Python impl 
      # it will do
      distribution = scipy.stats.cauchy()
      self._S = sprand.hashmap(s, n, distribution, dimension = 0)

  def _ppyapply(self, A, SA, dim):
    if dim == 0:
      SA1 = self._S * A
    if dim == 1:
      SA1 = A * self._S.T

    # We really want to use the out parameter of scipy.dot, but it does not seem 
    # to work (raises a ValueError)
    numpy.copyto(SA, SA1)

class WZT(_SketchTransform):
  """
  Woodruff-Zhang Transform. A variant of CountSketch (Clarkson-Woodruff Transform)
  using for low-distrition in Lp-norm. p is supplied as a parameter in the 
  constructor.

  :param n: Number of dimensions in input vectors.
  :param s: Number of dimensions in output vectors.
  :param p: Defines the norm for the embedding (Lp).
  :param defouttype: Default output type when using the * and / operators.

  *D. Woodruff* and *Q. Zhang*, **Subspace Embeddings and L_p Regression
  Using Exponential Random**, COLT 2013
  """

  class _WZTDistribution(object):
    def __init__(self, p):
      self._edist = scipy.stats.expon()
      self._bdist = scipy.stats.bernoulli(0.5)
      self._p = p

    def rvs(self, size):
      val = numpy.empty(size);
      for idx in range(0, size):
        val[idx] = (2 * self._bdist.rvs() - 1) * math.pow(self._edist.rvs(), 1/self._p)
      return val

  def __init__(self, n, s, p, defouttype=None):
    super(WZT, self)._baseinit("WZT", n, s, defouttype)

    if self._ppy:
      # The following is not memory efficient, but for a pure Python impl 
      # it will do
      distribution = WZT._WZTDistribution(p)
      self._S = sprand.hashmap(s, n, distribution, dimension = 0)      
    else:
      sketch_transform = c_void_p()
      _callsl(_lib.sl_create_sketch_transform, _ctxt_obj, "WZT", n, s, \
                byref(sketch_transform), ctypes.c_double(p))
      self._obj = sketch_transform.value

  def _ppyapply(self, A, SA, dim):
    if dim == 0:
      SA1 = self._S * A
    if dim == 1:
      SA1 = A * self._S.T

    # We really want to use the out parameter of scipy.dot, but it does not seem 
    # to work (raises a ValueError)
    numpy.copyto(SA, SA1)

class GaussianRFT(_SketchTransform):
  """
  Random Features Transform for the RBF Kernel. 

  :param n: Number of dimensions in input vectors.
  :param s: Number of dimensions in output vectors.
  :param sigma: bandwidth of the kernel.
  :param defouttype: Default output type when using the * and / operators.
    
  *A. Rahimi* and *B. Recht*, **Random Features for Large-scale
  Kernel Machines**, NIPS 2009
  """
  def __init__(self, n, s, sigma=1.0, defouttype=None):
    super(GaussianRFT, self)._baseinit("GaussianRFT", n, s, defouttype)

    self._sigma = sigma
    if self._ppy:
      self._T = JLT(n, s)
      self._b = numpy.matrix(numpy.random.uniform(0, 2 * pi, (s,1)))
    else:
      sketch_transform = c_void_p()
      _callsl(_lib.sl_create_sketch_transform, _ctxt_obj, "GaussianRFT", n, s, \
                byref(sketch_transform), ctypes.c_double(sigma))
      self._obj = sketch_transform.value

  def _ppyapply(self, A, SA, dim):
    self._T.apply(A, SA, dim)
    if dim == 0:
      bm = self._b * numpy.ones((1, SA.shape[1]))
    if dim == 1:
      bm = numpy.ones((SA.shape[0], 1)) * self._b.T
    SA[:, :] = sqrt(2.0 / self._s) * numpy.cos(SA * (sqrt(self._s)/self._sigma) + bm) 
    
class LaplacianRFT(_SketchTransform):
  """
  Random Features Transform for the Laplacian Kernel

  :param n: Number of dimensions in input vectors.
  :param s: Number of dimensions in output vectors.
  :param sigma: bandwidth of the kernel.
  :param defouttype: Default output type when using the * and / operators.

  *A. Rahimi* and *B. Recht*, **Random Features for Large-scale
  Kernel Machines**, NIPS 2009
  """
  def __init__(self, n, s, sigma=1.0, defouttype=None):
    super(LaplacianRFT, self)._baseinit("LaplacianRFT", n, s, defouttype)

    if not self._ppy:
      sketch_transform = c_void_p()
      _callsl(_lib.sl_create_sketch_transform, _ctxt_obj, "LaplacianRFT", n, s, \
                byref(sketch_transform), ctypes.c_double(sigma))
      self._obj = sketch_transform.value

class FastGaussianRFT(_SketchTransform):
  """
  Fast variant of Random Features Transform for the RBF Kernel. 

  Alternative class name: Fastfood

  :param n: Number of dimensions in input vectors.
  :param s: Number of dimensions in output vectors.
  :param sigma: bandwidth of the kernel.
  :param defouttype: Default output type when using the * and / operators.
    
  *Q. Le*, *T. Sarlos*, *A. Smola*, **Fastfood - Computing Hilbert Space 
  Expansions in Loglinear Time**, ICML 2013
  """
  def __init__(self, n, s, sigma=1.0, defouttype=None):
    super(FastGaussianRFT, self)._baseinit("FastGaussianRFT", n, s, defouttype);

    self._ppy = True
    self._blocks = int(math.ceil(float(s) / n))
    self._sigma = sigma
    self._b = numpy.matrix(numpy.random.uniform(0, 2 * pi, (s,1)))
    binary = scipy.stats.bernoulli(0.5)
    self._B = [2.0 * binary.rvs(n) - 1.0 for i in range(self._blocks)]
    self._G = [numpy.random.randn(n) for i in range(self._blocks)]
    self._P = [numpy.random.permutation(n) for i in range(self._blocks)]

  def _ppyapply(self, A, SA, dim):
    blks = [self._ppyapplyblk(A, dim, i) for i in range(self._blocks)]
    SA0 = numpy.concatenate(blks, axis=dim)
    if dim == 0:
      bm = self._b * numpy.ones((1, SA.shape[1]))
      if self._s < SA0.shape[0]:
        SA0 = SA0[:self._s, :]
    if dim == 1:
      bm = numpy.ones((SA.shape[0], 1)) * self._b.T
      if self._s < SA0.shape[1]:
        SA0 = SA0[:, :self._s]
    SA[:, :] = sqrt(2.0 / self._s) * numpy.cos(SA0 / (self._sigma * sqrt(self._n)) + bm) 

  def _ppyapplyblk(self, A, dim, i):
    B = scipy.sparse.spdiags(self._B[i], 0, self._n, self._n)
    G = scipy.sparse.spdiags(self._G[i], 0, self._n, self._n)
    P = self._P[i]

    if dim == 0:
      FBA = scipy.fftpack.dct(B * A, axis = 0, norm='ortho') * sqrt(self._n)
      FGPFBA = scipy.fftpack.dct(G * ABF[P, :], axis = 0, norm='ortho') * sqrt(self._n)
      return FGPFBA

    if dim == 1:
      ABF = scipy.fftpack.dct(A * B, axis = 1, norm='ortho') * sqrt(self._n)
      ABFPGF = scipy.fftpack.dct(ABF[:, P] * G, axis = 1, norm='ortho') * sqrt(self._n) 
      return ABFPGF



class PPT(_SketchTransform):
  """
  Pham-Pagh Transform - features sketching for the polynomial kernel.

  Alternative class name: TensorSketch

  :param n: Number of dimensions in input vectors.
  :param s: Number of dimensions in output vectors.
  :param q: degree of kernel
  :param c: kernel parameter.
  :param gamma: normalization coefficient.
  :param defouttype: Default output type when using the * and / operators.

  *N. Pham* and *R. Pagh*, **Fast and Scalable Polynomial Kernels via Explicit 
  Feature Maps**, KDD 2013
  """
  def __init__(self, n, s, q=3,  c=0, gamma=1, defouttype=None):
    super(PPT, self)._baseinit("PPT", n, s, defouttype);

    if c < 0:
      raise ValueError("c parameter must be >= 0")

    self._ppy = True
    self._q = q
    self._gamma = gamma
    self._c = c
    self._css = [CWT(n + (c > 0), s) for i in range(q)]

  def _ppyapply(self, A, SA, dim):
    if self._c != 0:
      sc = sqrt(self._c)
      if dim == 0:
        A = numpy.concatenate((A, sc * numpy.ones((1, A.shape[1]))))
      else:
        A = numpy.concatenate((A, sc * numpy.ones((A.shape[0], 1))), 1)
    
    P = numpy.ones(SA.shape)
    s = self._s
    for i in range(self._q):
      self._css[i].apply(sqrt(self._gamma) * A, SA, dim)
      P = numpy.multiply(P, numpy.fft.fft(SA, axis=dim) / sqrt(s))
    numpy.copyto(SA, numpy.fft.ifft(P, axis=dim).real * sqrt(s))
      
class URST(_SketchTransform):
  """
  Uniform Random Sampling Transform
  For now, only Pure Python implementation, and only sampling with replacement.

  Alternative class name: UniformSampler

  :param n: Number of dimensions in input vectors.
  :param s: Number of dimensions in output vectors.
  :param defouttype: Default output type when using the * and / operators.
  """
  def __init__(self, n, s, defouttype=None):
    super(URST, self)._baseinit("URST", n, s, defouttype);
    self._ppy = True
    self._idxs = numpy.random.permutation(n)[0:s]

  def _ppyapply(self, A, SA, dim):
    if dim == 0:
      SA[:, :] = A[self._idxs, :]
    if dim == 1:
      SA[:, :] = A[:, self._idxs]

class NURST(_SketchTransform):
  """
  Non-Uniform Random Sampling Transform
  For now, only Pure Python implementation, and only sampling with replacement.

  Alternative class name: NonUniformSampler

  :param n: Number of dimensions in input vectors.
  :param s: Number of dimensions in output vectors.
  :param p: Probability distribution on the n rows.
  :param defouttype: Default output type when using the * and / operators.
  """
  def __init__(self, n, s, p, defouttype=None):
    super(NURST, self)._baseinit("NURST", n, s, defouttype);
    if p.shape[0] != n:
      raise errors.InvalidParamterError("size of probability array should be exactly n")
    self._ppy = True
    self._idxs = scipy.stats.rv_discrete(values=(numpy.arange(0,n), p), \
                                         name = 'uniform').rvs(size=s)

  def _ppyapply(self, A, SA, dim):
    if dim == 0:
      SA[:, :] = A[self._idxs, :]
    if dim == 1:
      SA[:, :] = A[:, self._idxs]

#
# Additional names for various transforms.
#
SparseJLT = SJLT
FastJLT = JLT
CountSketch = CWT
RRT = GaussianRFT
Fastfood=FastGaussianRFT
TensorSketch = PPT
UniformSampler = URST
NonUniformSampler = NURST
