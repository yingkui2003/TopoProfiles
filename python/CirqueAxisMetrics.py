#-------------------------------------------------------------------------------
# Name:        WholeACMEcalculationsWithThresholds
# Purpose:     Derive all cirque metrics based on Cirque outlines, a DEM, and user-provided thresholds
#
# Author: Yingkui Li
# This program derive cirque related metrics based on cirque outlines and a DEM
# The first step is to determine the cirque threshold points
# The second step is to derive length and width info, as well as the area, and parameters
# The third step is to detive the 3D statistics and hypsometric parameters
# Some of the codes are revised based on the ACME codes by Ramon Pellitero and Matteo Spagnolo 2016
# 
# Created:     05/26/2023
# Copyright:   (c) Yingkui Li 2023
#-------------------------------------------------------------------------------

from __future__ import division
import arcpy
from arcpy import env
from arcpy.sa import *
import math
import time
import numpy as np
from scipy.optimize import curve_fit
from scipy import optimize
import matplotlib.pyplot as plt

arcpy.env.overwriteOutput = True
arcpy.env.XYTolerance= "0.01 Meters"

ArcGISPro = 0
arcpy.AddMessage("The current python version is: " + str(sys.version_info[0]))
if sys.version_info[0] == 2:  ##For ArcGIS 10, need to check the 3D and Spatial Extensions
    try:
        if arcpy.CheckExtension("Spatial")=="Available":
            arcpy.CheckOutExtension("Spatial")
        else:
            raise Exception ("not extension available")
            #print "not extension available"
    except:
        raise Exception ("unable to check out extension")
        #print "unable to check out extension"

    try:
        if arcpy.CheckExtension("3D")=="Available":
            arcpy.CheckOutExtension("3D")
        else:
            raise Exception ("not extension available")
            #print "not extension available"
    except:
        raise Exception ("unable to check out extension")
        #print "unable to check out extension"
elif sys.version_info[0] == 3:  ##For ArcGIS Pro
    ArcGISPro = 1
    #pass ##No need to Check
else:
    raise Exception("Must be using Python 2.x or 3.x")
    exit()   

temp_workspace = "in_memory"  
if ArcGISPro:
    temp_workspace = "memory"

# Polynomial Regression
def polyfit(x, y, degree):
    results = {}

    coeffs = np.polyfit(x, y, degree)

     # Polynomial Coefficients
    results['polynomial'] = coeffs.tolist()

    # r-squared
    p = np.poly1d(coeffs)
    # fit values, and mean
    yhat = p(x)                         # or [p(z) for z in x]
    ybar = np.sum(y)/len(y)          # or sum(y)/len(y)
    ssreg = np.sum((yhat-ybar)**2)   # or sum([ (yihat - ybar)**2 for yihat in yhat])
    sstot = np.sum((y - ybar)**2)    # or sum([ (yi - ybar)**2 for yi in y])
    results['determination'] = ssreg / sstot

    return results

def k_curve (x, c):
    return (1-x) * np.exp(c * x)

# normalized K_curve Regression
def k_curve_fit(x, y):
    popt, pcov = curve_fit(k_curve, x, y)
    c = popt[0]
    # r-squared
    yhat = k_curve(x, c)             # or [p(z) for z in x]
    ybar = np.sum(y)/len(y)          # or sum(y)/len(y)
    ssreg = np.sum((yhat-ybar)**2)   # or sum([ (yihat - ybar)**2 for yihat in yhat])
    sstot = np.sum((y - ybar)**2)    # or sum([ (yi - ybar)**2 for yi in y])
    R2 = ssreg / sstot
    if R2 > 1:
        R2 = 1/R2
    return (c, R2)

#---------------------------------------------------------------------------------------------------------------
# This function calculates the distance between two points
#--------------------------------------------------------------------------------------------------------------- 
def Dist(x1,y1,x2,y2):
    return math.sqrt(math.pow(math.fabs(x1-x2),2)+math.pow(math.fabs(y1-y2),2))

#------------------------------------------------------------------------------------------------------------
# This function check each line in the line feature and make sure the line is from low elevation to high
# elevation (Glacier streamline needs from low to high elevation in order to reconstruct the paleo ice thickness).
# It is revised from the codes by Pellitero et al.(2016) in GlaRe.
#------------------------------------------------------------------------------------------------------------
def Check_If_Flip_Line_Direction(line, dem):
    cellsize = arcpy.GetRasterProperties_management(dem,"CELLSIZEX")
    cellsize_int = int(float(cellsize.getOutput(0)))
    #arcpy.AddMessage("cellsize_int: " + str(cellsize_int))

    line3d = arcpy.env.scratchGDB + "\\line3d"
    arcpy.AddField_management(line, "Flip", "Long", "", "", "", "", "", "", "")

    arcpy.InterpolateShape_3d(dem, line, line3d, cellsize_int*3) 

    flip_list = []
    i = 0
    with arcpy.da.SearchCursor(line3d,["Shape@"]) as cursor:
        for row in cursor:
            startZ = row[0].firstPoint.Z
            #arcpy.AddMessage("startZ: " + str(startZ))
            endZ = row[0].lastPoint.Z
            #arcpy.AddMessage("endZ: " + str(endZ))

            if startZ >= endZ:  ##Flip = True use equal in case the start and end point are the same
                flip_list.append(1)
            else:  ##Flip = False
                flip_list.append(0)
            i += 1 

    del cursor
    if i>0:
        del row

    #arcpy.AddMessage(flip_list)
    #arcpy.AddMessage(str(sum(flip_list)))

    if sum(flip_list) > 0:
        with arcpy.da.UpdateCursor(line,["Flip"]) as cursor:
            i = 0
            for row in cursor:
                row[0] = flip_list[i]
                cursor.updateRow(row)
                i += 1 
        del row, cursor

        arcpy.MakeFeatureLayer_management(line, "lyrLines")
        arcpy.SelectLayerByAttribute_management("lyrLines", "NEW_SELECTION", '"Flip" > 0')
        #arcpy.AddMessage("The number of fliped lines is: " + str(sum(flip_list)))
        arcpy.FlipLine_edit("lyrLines")  ##Need to change to lyrLines
        arcpy.SelectLayerByAttribute_management("lyrLines", "CLEAR_SELECTION")

    arcpy.DeleteField_management (line, "Flip")
    arcpy.Delete_management(line3d)

###rdp only positive distance!!! for turning point detection
def Knickpoints_rdp(points, epsilon, turn_points, dists):
    # get the start and end points
    start = np.tile(np.expand_dims(points[0], axis=0), (points.shape[0], 1))
    end = np.tile(np.expand_dims(points[-1], axis=0), (points.shape[0], 1))
    linedist = Dist(start[0][0],start[0][1],end[0][0],end[0][1])
    dist_point_to_line = np.cross(end - start, points - start, axis=-1) / np.linalg.norm(end - start, axis=-1)
    max_idx = np.argmax(dist_point_to_line)
    max_value = dist_point_to_line[max_idx]##/linedist

    if abs(max_value) > epsilon:  ##the distance is at least 1 m from the line
        if max_value > 0:
             turn_points.append(points[max_idx])
             dists.append(max_value)

        partial_results_left = Knickpoints_rdp(points[:max_idx+1], epsilon, turn_points,dists)
        partial_results_right = Knickpoints_rdp(points[max_idx:], epsilon, turn_points,dists)
        
def turning_points_RDP(streamLength, streamZ, turning_points = 10, cluster_radius = 200):
    #plt.plot( streamLength, streamZ)
    #plt.show()

    stream_points = np.concatenate([np.expand_dims(streamLength, axis=-1), np.expand_dims(streamZ, axis=-1)], axis=-1)
    epsilon = 0.01
    turn_points = []
    turn_dists = []

    Knickpoints_rdp(stream_points, epsilon, turn_points, turn_dists)
    #arcpy.AddMessage(turn_points)
    #arcpy.AddMessage(turn_dists)
    

    if len(turn_points) < 1:
        #arcpy.AddMessage("The number of turning points is 0")
        return [], []
    
    new_x = np.array(turn_points)[:,0]  
    #arcpy.AddMessage(new_x)
    turn_point_idx = np.argsort(turn_dists)[::-1]

    t_pointsID = []
    t_dists = []

    while len(t_pointsID) < turning_points and len(turn_point_idx) > 0:
        dist = turn_dists[turn_point_idx[0]]
        if dist < 0.01: 
            break
        else:
            t_pointsID += [turn_point_idx[0]]
            t_dists.append(turn_dists[turn_point_idx[0]])
            cumLength = new_x[turn_point_idx[0]]

            trueidx = np.where(np.abs(new_x - cumLength) < cluster_radius)
            if len(trueidx[0])> 0:
                for i in range(len(trueidx[0])):
                    index = trueidx[0][i]
                    turn_point_idx = np.delete(turn_point_idx, np.where(turn_point_idx == index))
    t_points = []
    ##Find the original  point-ID infomation
    #arcpy.AddMessage("The number of turning points: " + str(len(t_pointsID)))
    #arcpy.AddMessage(t_pointsID)
    
    for i in range(len(t_pointsID)):
        points = turn_points[t_pointsID[i]]
        #arcpy.AddMessage(points[1])
        t_point_idx = np.where((streamZ-points[1]) < 1)[0][0]
        #arcpy.AddMessage(t_point_idx)
        
        t_points.append(t_point_idx)

    return t_points, t_dists

'''
def turning_points (LengthfromStart, PointZ, 10, 200):

    #logstreamlength = np.log(LengthfromStart[1:])
    t_points, t_dists = turning_points_RDP(LengthfromStart, PointZ, 3, cellsize_int*5) ##only top 3 turing points should be enough
    #t_points, t_ratios = turning_points_RDE(LengthfromStart, PointZ, 10, 200)
    #t_points, t_ratios = turning_points(LengthfromStart, PointZ, turning_points = 5, cluster_radius = cellsize_int*5)
    #t_points, t_ratios = turning_points_ConvexAngle(LengthfromStart, PointZ, 10, 200)

    for i in range(len(t_points)):
        idx = t_points[i] + 1 ##the idx should plus 1 because the t_points are only indexed except for the start and end points
        arcpy.
        #idx = t_points[i] ##+ 1 ##do not add 1 for the RDP method
        Ratio = t_ratios[i]
'''

##Main program
# Script arguments
InputDEM = arcpy.GetParameterAsText(0)
InputProfiles = arcpy.GetParameterAsText(1)
AdjustProfile = arcpy.GetParameterAsText(2)
min_height = arcpy.GetParameter(3)
OutputProfileMetrics  = arcpy.GetParameterAsText(4) ##Input turning points or cross sections around the outlet points
OutputConvexPoints  = arcpy.GetParameterAsText(5) ##Input turning points or cross sections around the outlet points
OutputHalfProfileMetrics  = arcpy.GetParameterAsText(6) ##Input turning points or cross sections around the outlet points
OutputFolder = arcpy.GetParameterAsText(7)
#environments

spatialref=arcpy.Describe(InputProfiles).spatialReference #get spat ref from input
arcpy.env.outputCoordinateSystem = spatialref #output coordinate system is taken from spat ref
arcpy.env.overwriteOutput = True #every new created file with the same name as an already existing file will overwrite the previous file
arcpy.env.XYTolerance= "1 Meters"
arcpy.env.scratchWorkspace=arcpy.env.scratchGDB #define a default folder/database where intermediate product will be stored

cellsize = arcpy.GetRasterProperties_management(InputDEM,"CELLSIZEX")
cellsize_float = float(cellsize.getOutput(0)) # use float cell size


arcpy.Delete_management(temp_workspace) ### Empty the in_memory
profile3D = temp_workspace + "\\profile3D"
arcpy.InterpolateShape_3d(InputDEM, InputProfiles, profile3D)
#if OutputHalfProfileMetrics != "":
#    #arcpy.AddMessage("Divide half valley profiles...")
#    #arcpy.InterpolateShape_3d(InputDEM, InputProfiles, temp_workspace + "\\profile3D")

lowest_X_coord = []
lowest_Y_coord = []
with arcpy.da.SearchCursor(profile3D, ["SHAPE@"]) as cursor:
    for row in cursor: ##Loop for each line
        PointX = []
        PointY = []
        PointZ = []
        for part in row[0]:
            for pnt in part:
                if pnt:
                    PointX.append(pnt.X)
                    PointY.append(pnt.Y)
                    PointZ.append(pnt.Z)

        pointZArr = np.array(PointZ).astype(int)
        pntXarr = np.array(PointX)
        pntYarr = np.array(PointY)

        ##Get the X Y coordinates of the lowest point
        min_Z = min(pointZArr)
        lowest_X_coord.append (pntXarr[pointZArr == min_Z][0])
        lowest_Y_coord.append (pntYarr[pointZArr == min_Z][0])

lowest_points = arcpy.CreateFeatureclass_management(temp_workspace + "", "lowest_points","POINT", "","","", spatialref)
arcpy.AddField_management(lowest_points, 'PntID', 'Long', 6) 

new_point_cursor = arcpy.da.InsertCursor(lowest_points, ('SHAPE@', 'PntID'))
for i in range(len(lowest_X_coord)):
    pnt = arcpy.Point(lowest_X_coord[i],lowest_Y_coord[i])
    new_point_cursor.insertRow([pnt, i])
del new_point_cursor        

if len(AdjustProfile) > 10:  ##Refine profiles 
    ##check the knickpoint idetification tool in AutoCirque
    ##This process will generate a new set of profiles for the following calculations
    #arcpy.InterpolateShape_3d(InputDEM, InputProfiles, temp_workspace + "\\profile3D")
    if "convex" in AdjustProfile:
        arcpy.AddMessage("Cut the cross sections by the largest convex points on each side...")
    else:
        arcpy.AddMessage("Cut the cross sections by the highest points on each side...")
    #FID_list = []
    X_coord = []
    Y_coord = []
    #min_X_coord = []
    #min_Y_coord = []
    pntType = []
    with arcpy.da.SearchCursor(temp_workspace + "\\profile3D", ["SHAPE@"]) as cursor:
        for row in cursor: ##Loop for each line
            PointX = []
            PointY = []
            LengthfromStart = []
            PointZ = []
            #FID_list.append(row[0])
            #lineLength = float(row[2])
            cumLength = 0
            for part in row[0]:
                pntCount = 0
                cumLength = 0
                segmentdis = 0
                for pnt in part:
                    if pnt:
                        if pntCount > 0:
                            cumLength += Dist(startx, starty, pnt.X, pnt.Y) 
                        PointX.append(pnt.X)
                        PointY.append(pnt.Y)
                        PointZ.append(pnt.Z)
                        LengthfromStart.append(cumLength)

                        startx = pnt.X
                        starty = pnt.Y
                        pntCount += 1

            ##Step 1: Split the cross section by the lowest points
            pointZArr = (np.array(PointZ)*100).astype(int)
            min_Z = min(pointZArr)

            
            ##the parameters for determine the turning points; May not necessary
            #step_height = 5

            ##Seperate the pointX to two arrays based on the lowest elevation
            array = np.append(pointZArr, np.inf)  # padding so we don't lose last element
            pointXarr = np.append(PointX, np.inf)  # padding so we don't lose last element
            pointYarr = np.append(PointY, np.inf)  # padding so we don't lose last element
            floatZarr = np.append(PointZ, np.inf)  # padding so we don't lose last element
            LengthArr = np.append(LengthfromStart, np.inf)
            

            split_indices = np.where(array == min_Z)[0]
            splitarray = np.split(array, split_indices + 1)
            splitpointXarr = np.split(pointXarr, split_indices + 1)
            splitpointYarr = np.split(pointYarr, split_indices + 1)
            splitpointZarr = np.split(floatZarr, split_indices + 1)
            splitlengtharr = np.split(LengthArr, split_indices + 1)
            #z_min = PointZ[split_indices[0]]

            #minPointX = pointXarr[split_indices][0]
            #minPointY = pointXarr[split_indices][0]
            #min_X_coord.append(minPointX)
            #min_Y_coord.append(minPointY)


            ##Cut the cross section by the lowest point and then to cut the highest points into each half
            k = 0
            for subarray in splitarray:
                if len(subarray) > 5: ##the half profile should at least have 5 points
                    half_pointZarr = subarray[:-1]

                    subpointXarr = splitpointXarr[k]
                    half_pointXarr = subpointXarr[:-1]
                    
                    subpointYarr = splitpointYarr[k]
                    half_pointYarr = subpointYarr[:-1]

                    sublengtharr = splitlengtharr[k]
                    half_lengtharr = sublengtharr[:-1]

                    z_max = max(half_pointZarr)
                    idx = np.where(half_pointZarr == z_max)[0][0]

                    ##Record the X and Y coordinates for the highest points
                    X_coord.append(half_pointXarr[idx])
                    Y_coord.append(half_pointYarr[idx])
                    pntType.append(1)  ##1: highest points

                    #plt.plot( half_lengtharr, half_pointZarr/100)
                    #plt.show()
                    if "convex" in AdjustProfile: 
                        #arcpy.AddMessage("Cut the cross sections by the largest convex points on each side...")
                        init_height = int(min_height)
                        if (half_pointZarr[0] > half_pointZarr[-1]): ##Left side of the profile
                            validpointZarr = half_pointZarr[idx:]
                            validpointXarr = half_pointXarr[idx:]
                            validpointYarr = half_pointYarr[idx:]
                            validlengtharr = half_lengtharr[idx:]
                            validlengtharr = validlengtharr - min(validlengtharr) ##normalize the length values
                        else: ##Right-side of the profile; reverse the order of the array
                            validpointZarr = np.flip(half_pointZarr[:idx+1])
                            validpointXarr = np.flip(half_pointXarr[:idx+1])
                            validpointYarr = np.flip(half_pointYarr[:idx+1])
                            validlengtharr = np.flip(half_lengtharr[:idx+1])
                            validlengtharr = max(validlengtharr) - validlengtharr ##normalize the length values

                        #plt.plot( validlengtharr, validpointZarr/100)
                        #plt.show()

                        ##Cut the the profile based on the (min_Z + init_height*100)
                        validpointZarr = np.append(validpointZarr, 0)  # padding so we don't lose last element
                        validpointXarr = np.append(validpointXarr, 0)  # padding so we don't lose last element
                        validpointYarr = np.append(validpointYarr, 0)  # padding so we don't lose last element
                        validlengtharr = np.append(validlengtharr, 0)  # padding so we don't lose last element
                        #LengthArr = np.append(LengthfromStart, np.inf)

                        split_indices = np.where(validpointZarr <= (min_Z + init_height*100))[0]
                        splithalfZarray = np.split(validpointZarr, split_indices + 1)
                        splithalfXarr = np.split(validpointXarr, split_indices + 1)
                        splithalfYarr = np.split(validpointYarr, split_indices + 1)
                        splithalfLarr = np.split(validlengtharr, split_indices + 1)

                        kk = 0
                        for subhalfarray in splithalfZarray:
                            if len(subhalfarray) > 3: ##the half profile should at least have 3 points
                                half_validZarr = subhalfarray[:-1]
                                
                                subhalfXarr = splithalfXarr[kk]
                                half_validXarr = subhalfXarr[:-1]
                                
                                subhalfYarr = splithalfYarr[kk]
                                half_validYarr = subhalfYarr[:-1]

                                subhalfLarr = splithalfLarr[kk]
                                half_validLarr = subhalfLarr[:-1]

                                #plt.plot( half_validLarr, half_validZarr/100)
                                #plt.show()

                                ##Apply the turning point method
                                #arcpy.AddMessage(half_validLarr)
                                #arcpy.AddMessage(half_validZarr)
                                t_points, t_dists = turning_points_RDP(half_validLarr, half_validZarr, 1, int(cellsize_float)*3) ##only top 1 turing points should be enough

                                ##only use the maximum dist (the first) to divide the profile
                                if len(t_points)> 0:
                                    ##record the x and Y coordiantes
                                    idx = t_points[0]
                                    #arcpy.AddMessage(idx)
                                    X_coord.append(half_validXarr[idx])
                                    Y_coord.append(half_validYarr[idx])
                                    pntType.append(2)  ##1: convex points
                            kk += 1

                k += 1        

    #arcpy.CopyFeatures_management(lowest_points, "d:\\temp\\lowest_points.shp") 
    bnd_points = arcpy.CreateFeatureclass_management(temp_workspace + "", "bnd_points","POINT", "","","", spatialref)
    arcpy.AddField_management(bnd_points, 'PntID', 'Long', 6) 
    arcpy.AddField_management(bnd_points, 'PntType', 'String', 10) 

    new_point_cursor = arcpy.da.InsertCursor(bnd_points, ('SHAPE@', 'PntID', 'PntType'))
    for i in range(len(X_coord)):
        pnt = arcpy.Point(X_coord[i],Y_coord[i])
        ptype = pntType[i]
        if ptype == 1:
            ptypeStr = "Highest"
        else:
            ptypeStr = "Convex"
            
        new_point_cursor.insertRow([pnt, i, ptypeStr])
    del new_point_cursor

    if OutputConvexPoints != "":
        arcpy.CopyFeatures_management(bnd_points, OutputConvexPoints) 

    arcpy.management.SplitLineAtPoint(InputProfiles, bnd_points, temp_workspace + "\\split_profiles", "1 Meters")
    fieldmappings = arcpy.FieldMappings()
    fieldmappings.addTable(InputProfiles)
    ##Should use the lowest points for the spatial join
    arcpy.SpatialJoin_analysis(temp_workspace + "\\split_profiles", lowest_points, OutputProfileMetrics, "JOIN_ONE_TO_ONE", "KEEP_COMMON", fieldmappings, "INTERSECT", "1 Meters", "#")
else:
    arcpy.CopyFeatures_management(InputProfiles, OutputProfileMetrics) 

if OutputHalfProfileMetrics != "":
    arcpy.management.SplitLineAtPoint(OutputProfileMetrics, lowest_points,OutputHalfProfileMetrics, "1 Meters")
    ##remove the small sections
    min_length = max(cellsize_float * 3, 100)
    with arcpy.da.UpdateCursor(OutputHalfProfileMetrics, 'SHAPE@LENGTH') as cursor:
        for row in cursor:
            if row[0] < min_length:
                cursor.deleteRow()
    del row, cursor

##Derive the whole profile metrics
arcpy.AddMessage("Add profile metric fields...")
Fieldlist=[]
ListFields=arcpy.ListFields(OutputProfileMetrics)

for x in ListFields:
    Fieldlist.append(x.baseName)

if "ProfileID" in Fieldlist:  ## count = 1
    pass
else:
    #add fieds to attribute tables to be populated with calculated values
    arcpy.AddField_management(OutputProfileMetrics, "ProfileID", "LONG", 10)
    arcpy.CalculateField_management(OutputProfileMetrics,"ProfileID",str("!"+str(arcpy.Describe(OutputProfileMetrics).OIDFieldName)+"!"),"PYTHON_9.3")

if OutputFolder != "":
    if "ProfilePlot" in Fieldlist:  ## count = 1
        pass
    else:
        #add fieds to attribute tables to be populated with calculated values
        arcpy.AddField_management(OutputProfileMetrics, "ProfilePlot", "TEXT", 20)

new_fields = ("Length","Height") ## All float variables 4
for field in new_fields:
    if field in Fieldlist:
        pass
    else:
        arcpy.AddField_management(OutputProfileMetrics, field, "DOUBLE", 10, 1)

##Axis variables
new_fields = ("WHRatio", "Asymmetry", "HHRatio", "Integral", "V_index") ## All float variables 4
for field in new_fields:
    if field in Fieldlist:
        pass
    else:
        arcpy.AddField_management(OutputProfileMetrics, field, "DOUBLE",10, 3)

  
##axis curve-fit variables 
new_fields = ("Quad_c", "Quad_r2", "VWDR_lna", "VWDR_b", "VWDR_r2") ##float variables with high digits
for field in new_fields:
    if field in Fieldlist:
        pass
    else:
        arcpy.AddField_management(OutputProfileMetrics, field, "DOUBLE",10, 4)

arcpy.AddMessage("Derive profile metrics...")
arcpy.InterpolateShape_3d(InputDEM, OutputProfileMetrics, profile3D) 

FID_list = []
PR_list = []
WH_list = []
HH_list = []
asymmetry_list = []
Amp_list = []
length_list = []
v_index_list = []

VWDR_a_list  = []
VWDR_b_list  = []
VWDR_r2_list = []
quad_c_list =  []
quad_r2_list = []

plot_list = []

with arcpy.da.SearchCursor(profile3D, ["ProfileID", "SHAPE@", "SHAPE@LENGTH"]) as cursor:
    i = 0
    line_length = 0
    for row in cursor: ##Loop for each line
        PointX = []
        PointY = []
        LengthfromStart = []
        PointZ = []
        fcID = row[0]
        FID_list.append(fcID)
        cumLength = 0
        line_length = row[2]
        length_list.append(line_length)
        for part in row[1]:
            pntCount = 0
            cumLength = 0
            segmentdis = 0
            for pnt in part:
                if pnt:
                    if pntCount > 0:
                        cumLength += Dist(startx, starty, pnt.X, pnt.Y) 
                    PointX.append(pnt.X)
                    PointY.append(pnt.Y)
                    PointZ.append(pnt.Z)
                    LengthfromStart.append(cumLength)

                    startx = pnt.X
                    starty = pnt.Y
                    pntCount += 1


        ##Save the cross section plot to outfolder
        if OutputFolder != "":
            fig, ax = plt.subplots()
            ax.plot(LengthfromStart, PointZ)
            ax.set_title(f'Cross Section: # ProfileID: {fcID}')
            ax.set_xlabel('Distance (m)')
            ax.set_ylabel('Elevation (m)')
            filename = OutputFolder + "\\ProfileID_" + str(fcID)+".png"
            fig.savefig(filename, dpi=300, bbox_inches='tight')
            plt.close(fig)  # Close the figure to save computer processing
            plotlink = "file:///" + filename
            plot_list.append(plotlink)

        ##Calculate the PR value  Need to determine the weighted average PR value????
        pointZArr = (np.array(PointZ)*100).astype(int) ##time 100 to make sure that the elevation can be accurate to 0.01 m
        #arcpy.AddMessage(max(LengthfromStart) - line_length)
        min_Z = min(pointZArr)
        array = np.append(pointZArr, np.inf)  # padding so we don't lose last element
        floatZArr = np.append(PointZ, np.inf)  # padding so we don't lose last element

        split_indices = np.where(array == min_Z)[0]  ##Divide the array into 2 halves based on the minimum Z
        z_min = PointZ[split_indices[0]] ##Get the float z_min
        #arcpy.AddMessage("z_min is: " + str(z_min))
        splitarray = np.split(floatZArr, split_indices + 1)

        #splitLengtharr = np.split(lengtharr, split_indices + 1)

        total_sections = len(pointZArr) - 1
        #arcpy.
        section_len = max(LengthfromStart) / total_sections
        profile_integal = 0
        #WH_ratio = 0
        weights = []
        valley_maxs = []
        valley_heights = []
        v_under_areas = []
        for subarray in splitarray:
            #arcpy.AddMessage(subarray)
            if len(subarray) > 1: #
                half_arr = np.append(subarray[:-1], z_min)
                #arcpy.AddMessage(len(half_arr))
                z_max = max(half_arr)
                valley_maxs.append(z_max)
                valley_heights.append(z_max - z_min)
                z_mean = sum(half_arr) / len(half_arr)
                pr = (z_mean - z_min) / (z_max - z_min + 0.001) ##to prevent the divide of zero
                #wh =  section_len*(len(half_arr)-1) / (z_max - z_min)
                weight = (len(half_arr)-1)/total_sections

                weights.append(weight)

                profile_integal += weight * pr
                #WH_ratio += weight * wh
                #total_weight += weight
                v_area_under = (z_max - z_min) * section_len*(len(half_arr)-1) * 0.5
                #arcpy.AddMessage(v_area_under)
                v_under_areas.append((z_max - z_min) * section_len*(len(half_arr)-1) * 0.5)
                
        if len(valley_heights) > 1:
            total_area = sum(valley_heights) * max(LengthfromStart) * 0.5
        else:
            total_area = sum(valley_heights) * max(LengthfromStart)
        #arcpy.AddMessage(v_under_areas)
        v_area = total_area - sum(v_under_areas)
        #arcpy.AddMessage(v_area)
        heights = np.array(PointZ) - z_min
        arcpy.AddMessage(heights[0])
        arcpy.AddMessage(heights[-1])
        hhratio = heights[0]/ (heights[-1]+0.001) ##the left height / divide the right height
        arcpy.AddMessage(hhratio)

        HH_list.append(hhratio)
        #arcpy.AddMessage(heights)
        under_area = sum(heights) / len(heights) * max(LengthfromStart) * 0.5
        cross_area =  total_area - under_area
        #arcpy.AddMessage(cross_area)
        vindex = cross_area / v_area - 1
        #arcpy.AddMessage(cross_area)
        v_index_list.append(vindex)
        
        asymmetry  = weights[0]/sum(weights)
        asymmetry_list.append(asymmetry)
        #arcpy.AddMessage("Total weight is: " + str(total_weight))
        PR_list.append(profile_integal)
        #form_ratio = max(LengthfromStart)/ (max(PointZ) - min(PointZ) + 0.001) ##May need to do the weighted average too!!!
        height = (max(PointZ) - min(PointZ))
        Amp_list.append(height)

        WH_ratio = line_length / height
        WH_list.append(WH_ratio)

        ##Derive VWDR Li et al (2001)
        ##Find the minimum of the Z-max
        max_elev = min(valley_maxs[0], valley_maxs[-1]) ##only consider the leftmost and rightmost sections of the cross section profile
        
        #arcpy.AddMessage(valley_maxs)
        #arcpy.AddMessage(max_elev)
        z_min = min(floatZArr)
        if max_elev < (z_min + 10): ##if the valley is only 10 m deep
            max_elev = max(valley_maxs)
        ##create the H and W lists
        height_list = []
        WDratio_list = []

        floatZArr2 = np.array(PointZ)
        #arcpy.AddMessage(floatZArr2)
        num = int((max_elev - z_min)/10)  ##use 10m interval for height lists and width list
        for i in range (num):
            elev = min(z_min + (i+1) * 10, max_elev)
            #arcpy.AddMessage(elev)
            first_index = np.argmax(floatZArr2 < elev)
            #arcpy.AddMessage(first_index)
            last_index = floatZArr2.size - np.argmax(floatZArr2[::-1] < elev) - 1
            #arcpy.AddMessage(last_index)
            ##For the first point X and Y
            pntX1 = PointX[first_index]
            pntY1 = PointY[first_index]
            elev1 = floatZArr2[first_index]
            #arcpy.AddMessage(pntX1)
            #arcpy.AddMessage(pntY1)
            #arcpy.AddMessage(elev1)
            
            pntX2 = PointX[first_index-1]
            pntY2 = PointY[first_index-1]
            elev2 = floatZArr2[first_index-1]

            #arcpy.AddMessage(pntX2)
            #arcpy.AddMessage(pntY2)
            #arcpy.AddMessage(elev2)

            pntXstart = pntX1 + (pntX2 - pntX1) / (elev2 - elev1) * (elev - elev1)            
            pntYstart = pntY1 + (pntY2 - pntY1) / (elev2 - elev1) * (elev - elev1)            
            #arcpy.AddMessage(pntXstart)
            #arcpy.AddMessage(pntYstart)
            if last_index < len(floatZArr2)-1:
                ##For the last point X and Y
                pntX1 = PointX[last_index]
                pntY1 = PointY[last_index]
                elev1 = floatZArr2[last_index]
                #arcpy.AddMessage(pntX1)
                #arcpy.AddMessage(pntY1)
                #arcpy.AddMessage(elev1)
                
                pntX2 = PointX[last_index+1]
                pntY2 = PointY[last_index+1]
                elev2 = floatZArr2[last_index+1]
                #arcpy.AddMessage(pntX2)
                #arcpy.AddMessage(pntY2)
                #arcpy.AddMessage(elev2)

                deltaX = (pntX2 - pntX1) / (elev2 - elev1) * (elev - elev1)
                deltaY = (pntY2 - pntY1) / (elev2 - elev1) * (elev - elev1)
                #arcpy.AddMessage(deltaX)
                #arcpy.AddMessage(deltaY)
                
                pntXend = pntX1 + (pntX2 - pntX1) / (elev2 - elev1) * (elev - elev1)            
                pntYend = pntY1 + (pntY2 - pntY1) / (elev2 - elev1) * (elev - elev1)            
                #arcpy.AddMessage(pntXend)
                #arcpy.AddMessage(pntYend)
            else:
                pntXend = PointX[last_index]
                pntYend = PointY[last_index]
            
            width = Dist(pntXstart,pntYstart,pntXend,pntYend)

            #arcpy.AddMessage("width is: " + str(width))
           
            height = (elev - z_min)
            wdratio = width / height
            #arcpy.AddMessage("width/depth ratio is: " + str(wdratio))

            WDratio_list.append(wdratio)
            height_list.append(height)

        #arcpy.AddMessage(height_list)    
        #arcpy.AddMessage(WDratio_list)    
            
        ##Derive the power law model fit for the longtitude profile 
        try:
            polyfit_results = polyfit(np.log(np.array(height_list)), np.log(np.array(WDratio_list)), 1)
            b = polyfit_results['polynomial'][0]
            a = np.exp(polyfit_results['polynomial'][1])
            R2 = polyfit_results['determination']
        except:
            #arcpy.AddMessage("There is an error in the curve fitting!")
            b = -999
            a = -999
            R2 = -999

        VWDR_a_list.append(a)
        VWDR_b_list.append(b)
        VWDR_r2_list.append(R2)                    

        
        ##Derive quadratic equation fit for the profile along the width line 03/15/2023
        polyfit_results = polyfit(LengthfromStart,PointZ, 2)
        c = polyfit_results['polynomial'][0]
        R2 = polyfit_results['determination']

        quad_c_list.append(c)
        quad_r2_list.append(R2)         

        i += 1

del row, cursor

if OutputFolder != "":
    fields = ("ProfileID", "Integral", "WHRatio", "Height", "Quad_c", "Quad_r2", "Asymmetry", "VWDR_lna", "VWDR_b", "VWDR_r2", "V_index", "Length", "HHRatio", "ProfilePlot")
else:
    fields = ("ProfileID", "Integral", "WHRatio", "Height", "Quad_c", "Quad_r2", "Asymmetry", "VWDR_lna", "VWDR_b", "VWDR_r2", "V_index", "Length", "HHRatio")

with arcpy.da.UpdateCursor(OutputProfileMetrics, fields) as cursor:
    for row in cursor:
        try:
            fid = FID_list.index(row[0])
            row[1] = f'{PR_list[fid]:.2f}'
            row[2] = f'{WH_list[fid]:.2f}'
            row[3] = f'{Amp_list[fid]:.1f}'
            row[4] = f'{quad_c_list[fid]:.4f}'
            row[5] = f'{quad_r2_list[fid]:.3f}'
            row[6] = f'{asymmetry_list[fid]:.3f}'
            row[7] = f'{VWDR_a_list[fid]:.4f}'
            row[8] = f'{VWDR_b_list[fid]:.4f}'
            row[9] = f'{HH_list[fid]:.2f}'
            VWDR_r2 = VWDR_r2_list[fid]
            row[9] = VWDR_r2
            row[10] = f'{v_index_list[fid]:.3f}'
            row[11] = f'{length_list[fid]:.1f}'
            row[12] = f'{HH_list[fid]:.1f}'
            if OutputFolder != "":
                row[13] = plot_list[fid]
            
            #update cursor
            cursor.updateRow(row)
        except:
            arcpy.AddMessage("There is an error in the calculation. Move to the next one")
            pass

del row, cursor

##Derive the half valley profile metrics
if OutputHalfProfileMetrics != "":
    #a "list" where the name of fields from the attributed table are copied in
    arcpy.AddMessage("Derive half valley profile metrics...")
    arcpy.AddMessage("Add half profile metric fields...")
    Fieldlist=[]
    ListFields=arcpy.ListFields(OutputHalfProfileMetrics)

    for x in ListFields:
        Fieldlist.append(x.baseName)

    if "ProfileID" in Fieldlist:  ## count = 1
        pass
    else:
        #add fieds to attribute tables to be populated with calculated values
        arcpy.AddField_management(OutputHalfProfileMetrics, "ProfileID", "LONG", 10)
        arcpy.CalculateField_management(OutputHalfProfileMetrics,"ProfileID",str("!"+str(arcpy.Describe(OutputHalfProfileMetrics).OIDFieldName)+"!"),"PYTHON_9.3")
        
    new_fields = ("Length","Height") ## All float variables 4
    for field in new_fields:
        if field in Fieldlist:
            pass
        else:
            arcpy.AddField_management(OutputHalfProfileMetrics, field, "DOUBLE", 10, 1)

    ##Axis variables
    new_fields = ("WHRatio", "Closure", "Integral", "Aspect","Gradient") ## All float variables 4
    for field in new_fields:
        if field in Fieldlist:
            pass
        else:
            arcpy.AddField_management(OutputHalfProfileMetrics, field, "DOUBLE",10, 2)

    ##axis curve-fit variables 
    new_fields = ("Exp_lna","Exp_b","Exp_r2","Pow_lna", "Pow_b", "Pow_r2","Kcurve_c","Kcurve_r2","SL", "SL_r2") ##float variables with high digits
    for field in new_fields:
        if field in Fieldlist:
            pass
        else:
            arcpy.AddField_management(OutputHalfProfileMetrics, field, "DOUBLE",10, 4)

    ##Check the direction and flip the length from low to high elevations
    arcpy.AddMessage("Check profile direction and flip it from low to high elevations if necessary...")
    Check_If_Flip_Line_Direction(OutputHalfProfileMetrics, InputDEM)

    arcpy.AddMessage("Derive half profile metrics...")
    arcpy.InterpolateShape_3d(InputDEM, OutputHalfProfileMetrics, temp_workspace + "\\profile3D") 

    FID_list = []
    HLHI_list = []
    HLAsp_list = []
    P_clos_list = []
    Amplitude_list = []
    profgrad_list = []
    length_list = []
    WH_list = []

    exp_a_list = []
    exp_b_list = []
    exp_r2_list = []

    pow_a_list = []
    pow_b_list = []
    pow_r2_list = []

    kcurve_c_list = []
    kcurve_r2_list = []
    SL_list = []
    SL_r2_list = []

    with arcpy.da.SearchCursor(temp_workspace + "\\profile3D", ["ProfileID", "SHAPE@", "SHAPE@Length"]) as cursor:
        i = 0
        for row in cursor: ##Loop for each line
            #arcpy.AddMessage("Profile #" + str(i+1))
            PointX = []
            PointY = []
            LengthfromStart = []
            PointZ = []
            FID_list.append(row[0])
            lineLength = float(row[2])
            length_list.append(lineLength)
            cumLength = 0
            for part in row[1]:
                pntCount = 0
                cumLength = 0
                segmentdis = 0
                for pnt in part:
                    if pnt:
                        if pntCount > 0:
                            cumLength += Dist(startx, starty, pnt.X, pnt.Y) 
                        PointX.append(pnt.X)
                        PointY.append(pnt.Y)
                        PointZ.append(pnt.Z)
                        LengthfromStart.append(cumLength)

                        startx = pnt.X
                        starty = pnt.Y
                        pntCount += 1
            ##Calculate the HI value
            #arcpy.AddMessage(len(PointZ))
            max_Z = max(PointZ)
            min_Z = min(PointZ)
            mean_Z = sum(PointZ) / len(PointZ)
            HI = (mean_Z - min_Z) / (max_Z - min_Z)+ 0.001 ##add 0.001 to avoid the divide of zero
            #arcpy.AddMessage("High-length HI: " + str(HI))
            HLHI_list.append(HI)

            height = max_Z - min_Z
            Amplitude_list.append(height)

            whratio = lineLength / height
            WH_list.append(whratio)
            
            gradient = 180.0/math.pi * math.atan((max_Z - min_Z)/max(LengthfromStart))

            profgrad_list.append(gradient)

            ##Calculate the HL-Aspect
            #dz  = PointZ[-1] - PointZ[0]
            dx  = PointX[0] - PointX[-1]
            dy  = PointY[0] - PointY[-1]
            #arcpy.AddMessage(str(dx))
            #arcpy.AddMessage(str(dy))

            aspect = 180.0/math.pi * math.atan2(dy, dx)
            #arcpy.AddMessage("Aspect is: " + str(aspect))
            if aspect < 90:
                adj_aspect = 90.0 - aspect
            else:
                adj_aspect = 360 + 90.0 - aspect
            HLAsp_list.append(adj_aspect)

            ##Derive the exponential model fit for the longtitude profile 03/15/2023
            pointH = [y - min_Z for y in PointZ]

            HArr = np.array(pointH)
            LenArr = np.array(LengthfromStart)

            validHArr = HArr[HArr > 0]
            validLenArr = LenArr[HArr > 0]
            
            try:
                polyfit_results = polyfit(validLenArr, np.log(validHArr), 1)
                b = polyfit_results['polynomial'][0]
                a = np.exp(polyfit_results['polynomial'][1])
                R2 = polyfit_results['determination']
            except:
                #arcpy.AddMessage("There is an error!")
                b = -999
                a = -999
                R2 = -999

            exp_a_list.append(a)
            exp_b_list.append(b)
            exp_r2_list.append(R2)

            ##Derive the power law model fit for the longtitude profile 
            try:
                polyfit_results = polyfit(np.log(validLenArr), np.log(validHArr), 1)
                b = polyfit_results['polynomial'][0]
                a = np.exp(polyfit_results['polynomial'][1])
                R2 = polyfit_results['determination']
            except:
                #arcpy.AddMessage("There is an error!")
                b = -999
                a = -999
                R2 = -999

            pow_a_list.append(a)
            pow_b_list.append(b)
            pow_r2_list.append(R2)
          
            ###Calculate the profile closure
            startx = np.array(LengthfromStart[0:-1])
            endx = np.array(LengthfromStart[1:])
            startz = np.array(PointZ[0:-1])
            endz = np.array(PointZ[1:])
            dzdx = (endz - startz)/(endx - startx)

            slopes = 180/np.pi * np.arctan(dzdx)

            #arcpy.AddMessage(slopes)
            if len(slopes) > 3:
                min_slp = np.min(slopes[0:3])
                max_slp = np.max(slopes[-3:])
            else:
                min_slp = np.min(slopes)
                max_slp = np.max(slopes)

            p_close = max_slp - min_slp
            #arcpy.AddMessage("the profile closure is: " + str(p_close))
            P_clos_list.append(p_close)

            #K-curve-fit
            max_len = max(LengthfromStart)
            PointZ.reverse()
            LengthfromStart.reverse()
            normalH = np.array([(y - min_Z)/(max_Z - min_Z) for y in PointZ])
            normalLen = np.array([(max_len - y) /(max_len) for y in LengthfromStart])
            
            fit_results = k_curve_fit(normalLen, normalH)
            c = fit_results[0]
            R2 = fit_results[1]

            kcurve_c_list.append(c)
            kcurve_r2_list.append(R2)

            ##derive the SL index?????
            pointH = [y - min_Z for y in PointZ] ##Already reversed in the previous step
            #arcpy.AddMessage(pointH)
            #pointH.reverse()
            #arcpy.AddMessage(pointH)

            #LengthfromStart.reverse() ##Already reversed in the previous step

            pointHArr = np.array(pointH)
            ReverseLengthArr = np.array([(max_len - y) for y in LengthfromStart])

            validHArr = pointHArr[pointHArr > 0]
            #arcpy.AddMessage(validHArr)
            validLenArr = ReverseLengthArr[ReverseLengthArr > 0]
            #arcpy.AddMessage(validLenArr)
            #plt.plot( validLenArr, validHArr)
            #plt.show()
            #plt.plot( np.log(validLenArr), validHArr)
            #plt.show()

            try:
                polyfit_results = polyfit(np.log(validLenArr), validHArr, 1)
                sl = polyfit_results['polynomial'][0]
                #c = np.exp(polyfit_results['polynomial'][1])
                R2 = polyfit_results['determination']
            except:
                #arcpy.AddMessage("There is an error!")
                sl = -999
                #a = -999
                R2 = -999

            SL_list.append(a)
            #SL_c_list.append(b)
            SL_r2_list.append(R2)
            
            i += 1

    del row, cursor

    fields = ("ProfileID", "Closure", "Integral", "Aspect", "Height", "Gradient", "Exp_lna","Exp_b","Exp_r2","Pow_lna", "Pow_b", "Pow_r2","Kcurve_c","Kcurve_r2","SL", "SL_r2", "Length", "WHRatio")

    with arcpy.da.UpdateCursor(OutputHalfProfileMetrics, fields) as cursor:
        for row in cursor:
            try:
                fid = FID_list.index(row[0])
                row[1] = f'{P_clos_list[fid]:.1f}'
                row[2] = f'{HLHI_list[fid]:.2f}'
                row[3] = f'{HLAsp_list[fid]:.1f}'
                row[4] = f'{Amplitude_list[fid]:.1f}'
                row[5] = f'{profgrad_list[fid]:.1f}'

                row[6] = f'{exp_a_list[fid]:.4f}'
                row[7] = f'{exp_b_list[fid]:.4f}'
                row[8] = f'{exp_r2_list[fid]:.3f}'

                row[9] = f'{pow_a_list[fid]:.4f}'
                row[10] = f'{pow_b_list[fid]:.4f}'
                row[11] = f'{pow_r2_list[fid]:.3f}'

                row[12] = f'{kcurve_c_list[fid]:.4f}'
                row[13] = f'{kcurve_r2_list[fid]:.3f}'
                row[14] = f'{SL_list[fid]:.4f}'
                row[15] = f'{SL_r2_list[fid]:.3f}'

                row[16] = f'{length_list[fid]:.1f}'
                row[17] = f'{WH_list[fid]:.2f}'

                #update cursor
                cursor.updateRow(row)
            except:
                #arcpy.AddMessage("There is an error in the calculation. Move to the next one")
                pass
    del row, cursor

arcpy.Delete_management(temp_workspace) ### Empty the in_memory












