#
# Find FFTW includes and library
#
# FFTW
# It can be found at:
#
# HDF5_INCLUDE_DIR - where to find hdf5.h
# HDF5_LIBRARY     - qualified libraries to link against.
# HDF5_FOUND       - do not attempt to use if "no" or undefined.

FIND_PATH(HDF5_INCLUDE_DIR hdf5.h
  PATHS $ENV{HDF5_ROOT}/include
  NO_DEFAULT_PATH
)

FIND_PATH(HDF5_INCLUDE_DIR hdf5.h
  /usr/include
  /usr/local/include
)

FIND_LIBRARY(HDF5_LIBRARY libhdf5.a
  PATHS $ENV{HDF5_ROOT}/lib
  NO_DEFAULT_PATH
)

FIND_LIBRARY(HDF5_LIBRARY libhdf5.a
  /usr/lib
  /usr/local/lib
)

FIND_LIBRARY(HDF5_CPP_LIBRARY libhdf5_cpp.a
  PATHS $ENV{HDF5_ROOT}/lib
  NO_DEFAULT_PATH
)

FIND_LIBRARY(HDF5_CPP_LIBRARY libhdf5_cpp.a
  /usr/lib
  /usr/local/lib
)

IF(HDF5_INCLUDE_DIR AND HDF5_LIBRARY AND HDF5_CPP_LIBRARY)
  set(HDF5_FOUND "YES")
  set(HDF5_LIBRARIES
    ${HDF5_LIBRARY}
    ${HDF5_CPP_LIBRARY}
  )
ENDIF(HDF5_INCLUDE_DIR AND HDF5_LIBRARY AND HDF5_CPP_LIBRARY)

IF (HDF5_FOUND)
  IF (NOT HDF5_FIND_QUIETLY)
    MESSAGE(STATUS "Found HDF5: ${HDF5_LIBRARIES}")
  ENDIF (NOT HDF5_FIND_QUIETLY)
ELSE (HDF5_FOUND)
  IF (HDF5_FIND_REQUIRED)
    MESSAGE(STATUS "HDF5 not found!")
  ENDIF (HDF5_FIND_REQUIRED)
ENDIF (HDF5_FOUND)
