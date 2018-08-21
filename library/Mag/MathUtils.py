import numpy as np
import scipy as sp
from scipy.spatial import cKDTree
from SimPEG.Utils import mkvc, speye
from scipy.sparse.linalg import bicgstab
import matplotlib.pyplot as plt


def rotationMatrix(inc, dec, normal=True):
    """
        Take an inclination and declination angle and return a rotation matrix

    """

    phi = -np.deg2rad(np.asarray(inc))
    theta = -np.deg2rad(np.asarray(dec))

    Rx = np.asarray([[1, 0, 0],
                    [0, np.cos(phi), -np.sin(phi)],
                    [0, np.sin(phi), np.cos(phi)]])

    Rz = np.asarray([[np.cos(theta), -np.sin(theta), 0],
                    [np.sin(theta), np.cos(theta), 0],
                    [0, 0, 1]])

    if normal:
        R = Rz.dot(Rx)
    else:
        R = Rx.dot(Rz)

    return R


def dipazm_2_xyz(dip, azm_N):
    """
    dipazm_2_xyz(dip,azm_N)

    Function converting degree angles for dip and azimuth from north to a
    3-components in cartesian coordinates.

    INPUT
    dip     : Value or vector of dip from horizontal in DEGREE
    azm_N   : Value or vector of azimuth from north in DEGREE

    OUTPUT
    M       : [n-by-3] Array of xyz components of a unit vector in cartesian

    Created on Dec, 20th 2015

    @author: dominiquef
    """

    # Modify azimuth from North to Cartesian-X
    azm_X = (450. - azm_N) % 360.

    D = np.deg2rad(np.asarray(dip))
    I = np.deg2rad(azm_X)

    M = np.zeros(3)
    M[0] = np.cos(D) * np.cos(I)
    M[1] = np.cos(D) * np.sin(I)
    M[2] = np.sin(D)

    return M


def rotate(xyz, center, theta, phi):
    """
      Rotate scatter points in column format around a center location

      INPUT
      :param: xyz nDx3 matrix
      :param: center xyz location of rotation
      :param: theta angle rotation around x-axis
      :param: phi angle rotation around z-axis

    """
    xyz -= np.kron(np.ones((xyz.shape[0], 1)), np.r_[center])

    R = rotationMatrix(-theta, phi)

    xyzRot = R.dot(xyz.T).T + np.kron(np.ones((xyz.shape[0], 1)), np.r_[center])

    return xyzRot


def minCurvatureInterp(
    locs, data,
    vectorX=None, vectorY=None, vectorZ=None, gridSize=10,
    tol=1e-5, iterMax=None, method='spline'
):
    """
    Interpolate properties with a minimum curvature interpolation
    :param locs:  numpy.array of size n-by-3 of point locations
    :param data: numpy.array of size n-by-m of values to be interpolated
    :param vectorX: numpy.ndarray Gridded locations along x-axis [Default:None]
    :param vectorY: numpy.ndarray Gridded locations along y-axis [Default:None]
    :param vectorZ: numpy.ndarray Gridded locations along z-axis [Default:None]
    :param gridSize: numpy float Grid point seperation in meters [DEFAULT:10]
    :param method: 'relaxation' || 'spline' [Default]
    :param tol: float tol=1e-5 [Default] Convergence criteria
    :param iterMax: int iterMax=None [Default] Maximum number of iterations

    :return: numpy.array of size nC-by-m of interpolated values

    """

    def av_extrap(n):
        """Define 1D averaging operator from cell-centers to nodes."""
        Av = (
            sp.sparse.spdiags(
                (0.5 * np.ones((n, 1)) * [1, 1]).T,
                [-1, 0],
                n + 1, n,
                format="csr"
            )
        )
        Av[0, 1], Av[-1, -2] = 0.5, 0.5
        return Av

    def aveCC2F(grid):
        "Construct the averaging operator on cell cell centers to faces."
        if grid.ndim == 1:
            aveCC2F = av_extrap(grid.shape[0])
        elif grid.ndim == 2:
            aveCC2F = sp.vstack((
                sp.kron(speye(grid.shape[1]), av_extrap(grid.shape[0])),
                sp.kron(av_extrap(grid.shape[1]), speye(grid.shape[0]))
            ), format="csr")
        elif grid.ndim == 3:
            aveCC2F = sp.vstack((
                kron3(
                    speye(grid.shape[2]), speye(grid.shape[1]), av_extrap(grid.shape[0])
                ),
                kron3(
                    speye(grid.shape[2]), av_extrap(grid.shape[1]), speye(grid.shape[0])
                ),
                kron3(
                    av_extrap(grid.shape[2]), speye(grid.shape[1]), speye(grid.shape[0])
                )
            ), format="csr")
        return aveCC2F

    assert locs.shape[0] == data.shape[0], ("Number of interpolated locs " +
                                            "must match number of data")

    if vectorY is not None:
        assert locs.shape[1] >= 2, (
                "Found vectorY as an input." +
                " Point locations must contain X and Y coordinates."
            )

    if vectorZ is not None:
        assert locs.shape[1] == 3, (
                "Found vectorZ as an input." +
                " Point locations must contain X, Y and Z coordinates."
            )

    ndim = locs.shape[1]

    # Define a new grid based on data extent
    if vectorX is None:
        xmin, xmax = locs[:, 0].min(), locs[:, 0].max()
        nCx = int((xmax-xmin)/gridSize)
        vectorX = xmin+np.cumsum(np.ones(nCx) * gridSize)

    if vectorY is None and ndim >= 2:
        ymin, ymax = locs[:, 1].min(), locs[:, 1].max()
        nCy = int((ymax-ymin)/gridSize)
        vectorY = ymin+np.cumsum(np.ones(nCy) * gridSize)

    if vectorZ is None and ndim == 3:
        zmin, zmax = locs[:, 2].min(), locs[:, 2].max()
        nCz = int((zmax-zmin)/gridSize)
        vectorZ = zmin+np.cumsum(np.ones(nCz) * gridSize)

    if ndim == 3:
        gridCx, gridCy, gridCz = np.meshgrid(vectorX, vectorY, vectorZ)
        gridCC = np.c_[mkvc(gridCx), mkvc(gridCy), mkvc(gridCz)]
    elif ndim == 2:
        gridCx, gridCy = np.meshgrid(vectorX, vectorY)
        gridCC = np.c_[mkvc(gridCx), mkvc(gridCy)]
    else:
        gridCC = vectorX

    # Build the cKDTree for distance lookup
    tree = cKDTree(locs)
    # Get the grid location
    d, ind = tree.query(gridCC, k=1)

    if method == 'relaxation':

        Ave = aveCC2F(gridCx)

        count = 0
        residual = 1.

        m = np.zeros((gridCC.shape[0], data.shape[1]))

        # Begin with neighrest primers
        for ii in range(m.shape[1]):
            # F = NearestNDInterpolator(mesh.gridCC[ijk], data[:, ii])
            m[:, ii] = data[ind, ii]

        while np.all([count < iterMax, residual > tol]):
            for ii in range(m.shape[1]):
                # F = NearestNDInterpolator(mesh.gridCC[ijk], data[:, ii])
                m[:, ii] = data[ind, ii]
            mtemp = m
            m = Ave.T * (Ave * m)
            residual = np.linalg.norm(m-mtemp)/np.linalg.norm(mtemp)
            count += 1

        return gridCC, m

    elif method == 'spline':

        ndat = locs.shape[0]
        # nC = int(nCx*nCy)

        A = np.zeros((ndat, ndat))
        for i in range(ndat):

            r = (locs[i, 0] - locs[:, 0])**2. + (locs[i, 1] - locs[:, 1])**2. +1e-8
            A[i, :] = r.T * (np.log((r.T)**0.5) - 1.)

        # Solve system for the weights
        w = bicgstab(A, data, tol=1e-6)

        # Compute new solution
        # Reformat the line data locations but skip every n points for test
        nC = gridCC.shape[0]
        m = np.zeros(nC)

        # We can parallelize this part later
        for i in range(nC):

            r = (gridCC[i, 0] - locs[:, 0])**2. + (gridCC[i, 1] - locs[:, 1])**2. + 1e+4
            m[i] = np.sum(w[0] * r.T * (np.log((r.T)**0.5) - 1.))

        return gridCC, m.reshape(gridCx.shape, order='F')

    else:

        NotImplementedError("Only methods 'relaxation' || 'spline' are available" )


def decimalDegrees2DMS(value, type):
    """
        Converts a Decimal Degree Value into
        Degrees Minute Seconds Notation.

        Pass value as double
        type = {Latitude or Longitude} as string

        returns a string as D:M:S:Direction
        created by: anothergisblog.blogspot.com
    """
    degrees = int(value)
    submin = abs( (value - int(value) ) * 60)
    minutes = int(submin)
    subseconds = abs((submin-int(submin)) * 60)
    direction = ""
    if type == "Longitude":
        if degrees < 0:
            direction = "W"
        elif degrees > 0:
            direction = "E"
        else:
            direction = ""
    elif type == "Latitude":
        if degrees < 0:
            direction = "S"
        elif degrees > 0:
            direction = "N"
        else:
            direction = ""
    notation = str(degrees) + ":" + str(minutes) + ":" +\
               str(subseconds)[0:5] + "" + direction
    return notation


def estimateDepth(grid):
    """
        Function to estimate depth of anomalies
        from tilt angle. Distance between the 0 and
        pi/4 contour.

        INPUT
        :object: grid Grid object from

        OUTPUT
        :xyDepth:
    """

    tilt = grid.tiltAngle

    X, Y = np.meshgrid(grid.hx, grid.hy)


    C_0 = plt.contour(X, Y, tilt, levels=[0], colors='k')
    C_45 = plt.contour(X, Y, tilt, levels=[np.pi/4.], colors='r')
    plt.close()

    # Get zero contour nodes
    # xy0 = np.vstack(C_0.allsegs[0])

    # Get 45 contour nodes
    xy45 = np.vstack(C_45.allsegs[0])

    # Create ckDtree for shortest distance
    tree = cKDTree(xy45)


    # Compute shortest distance between pair of points
    xy = []
    dist = []
    for contour in C_0.allsegs[0]:

        # Query two closest points to each nodes of zero contour
        d, indx = tree.query(contour, k=2)

        length = (
            (xy45[indx[:, 1], 0] - xy45[indx[:, 0], 0])**2. +
            (xy45[indx[:, 1], 1] - xy45[indx[:, 0], 1])**2.)

        indL = length > 0

        dist += [np.abs(
            (xy45[indx[indL, 1], 1] - xy45[indx[indL, 0], 1])*contour[indL, 0] -
            (xy45[indx[indL, 1], 0] - xy45[indx[indL, 0], 0])*contour[indL, 1] +
            xy45[indx[indL, 1], 0] * xy45[indx[indL, 0], 1] -
            xy45[indx[indL, 1], 1] * xy45[indx[indL, 0], 0]) / length[indL]**0.5]

        xy += [contour[indL, :]]

    return xy, dist

