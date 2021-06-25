""" Example script showing how to detect HeatWaves

[ADD SOME DESCRIPTIN HERE]
"""
import numpy as np
import netCDF4 as nc
import matplotlib as mpl
import matplotlib.pyplot as plt
import sys
import os
import cftime
import datetime as dt
from scipy import signal
import scipy.ndimage as ndimage

sloth_path='../'
sys.path.append(sloth_path)
import sloth

###############################################################################
#### Define some paths, filenames, options, etc
###############################################################################
dailyMeanFile  = f'../data/example_ClimateMeans/intervalMean_day.npy'
dailyTimeFile  = f'../data/example_ClimateMeans/intervalTime_day.npy'
refClimatology = f'../data/example_ClimateMeans/climate_day.npy'

# For full years and monthly means --> NoI 12 (12 month per year)
# For full years and daily means   --> NoI 365 (365 days per year)
# We do use daily means for full years
NoI = 365

# Year(s) to investigate for HeatWaves (HW)
hwYears = [1979,1980,1981,1982,1983,1984]

# HW investigation performed with this script is based on percentile 
# calculation. To 'smooth' the calculation the percentiles are calculated not
# for the time-series of a single day, but as kind of running-percentile for
# a 'window' of X days
window_percentile = 5

# A single hot day crossing the 90p threshold is not a HW, minDuration does
# define how many 'too hot days' in a row building a HW
minDuration = 6

###############################################################################
##### Read in climatology calculated with 'examples_CalculateClimateMeans.py
###############################################################################
try:
    dailyMean = np.load(dailyMeanFile)
    print(f'dailyMean.shape: {dailyMean.shape}')
    dailyTime = np.load(dailyTimeFile, allow_pickle=True)
    print(f'dailyTime.dtype: {dailyTime.dtype}')
    clima = np.load(refClimatology)

    # mask holding 1 where land 
    mask = np.zeros_like(clima[0])
    mask[clima[0]!=np.nan] = 1
    landPixels = np.sum(mask)
except FileNotFoundError:
    print(f'ERROR: not all needed files were not found: EXIT')
    sys.exit()


###############################################################################
#### preparing HeatWave investigation
###############################################################################
# HeatWave investigation performed in this script is based on the 90p 
# percentile of the climatology. 
# Create array holding the 90p percentiles, which shape is [365, Y, X] for 
# our case (NoI=365)
climaDim_90p = [NoI]
climaDim_90p = climaDim_90p + [dim for dim in dailyMean[0].shape]
clima_90p_raw = np.empty(climaDim_90p)
print(f'intervalClima_90p.shape: {clima_90p_raw.shape}')

# To 'smooth' the 90p percentile, a window around each day is taken into 
# account, that percentiles are not calculated out of the time-series of a 
# single day, but as kind of running-percentile for a 'window' of X days.
# A 'window' of e.g. 5 is actual a day increment by +- 2 days where incWindow 
# is calculated as incWindow = window_percentile//2 (integer devision)
incWindow = window_percentile // 2
for curr_day in range(NoI):
    print(f'handling day: {curr_day}')
    # tmp variable holding all data within the 'window'
    tmpWindow = None
    # Gathering the window.
    # +1 because python does exclude the last step
    for i in range(curr_day-incWindow, curr_day+incWindow+1):
        # skip if i is out of range for dailyMean
        if i < 0 or i > dailyMean.shape[0]:
            continue
        if tmpWindow is None:
            tmpWindow = dailyMean[i::NoI]
        else:
            tmpWindow = np.append(tmpWindow, dailyMean[i::NoI], axis=0)
    # Calculating the 'windowed' 90p percentile for each day a year. 
    clima_90p_raw[curr_day]=np.percentile(tmpWindow, 90, axis=0)
    # print('highest 90p percentile over entire domain: ',np.nanmax(clima_90p[curr_day]))


# loop over individual years HW-detection should be applied
HWevents = {}
ALL_event_label_counts = []
ALL_event_labels = []
for hwYear in hwYears:
    ###############################################################################
    #### Extract year of interests for HW analysis (out of entire daily-means)
    ###############################################################################
    boolSlice = [ hwYear == item.year for item in dailyTime]
    dailyMean_hw = dailyMean[boolSlice]
    dailyTime_hw = dailyTime[boolSlice]

    ###############################################################################
    #### Filter for some periods as JJA etc.
    ###############################################################################
    # currently hard-coded to JJA
    timeSlices = [item.month in [6,7,8] for item in dailyTime_hw]
    dailyMean_hw     = dailyMean_hw[timeSlices]
    dailyTime_hw     = dailyTime_hw[timeSlices]
    nc_time_units    = f'days since {dailyTime_hw[0].strftime("%Y-%m-%d")}'
    nc_time_calendar = '365_day'
    nc_time_hw       = cftime.date2num(dailyTime_hw, units=nc_time_units, calendar=nc_time_calendar)
    clima_90p        = clima_90p_raw[timeSlices]

    ###############################################################################
    #### Prepare output netCDF file to later appand different variables to
    ###############################################################################
    # For mor detailed information about how createNetCDF() does work, see
    # sloth/toolBox.py --> createNetCDF()
    saveFile=f'../data/example_HWevents/Events_{hwYear}.nc'
    netCDFFileName = sloth.toolBox.createNetCDF(saveFile, domain='EU11',
        timeCalendar=nc_time_calendar, timeUnit=nc_time_units,
        author='Niklas WAGNER', contact='n.wagner@fz-juelich.de',
        institution='FZJ - IBG-3', history=f'Created: {dt.datetime.now().strftime("%Y-%m-%d %H:%M")}',
        description='This files does contain information about heat-waves based on 90p thereshold',
        source='---', NBOUNDCUT=4)

    ###############################################################################
    #### Calculate events / where daily mean exceeds the 90p threshold
    ###############################################################################
    exceed_bool = dailyMean_hw - clima_90p
    exceed_bool[exceed_bool<=0] = False
    exceed_bool[exceed_bool>0] = True
    # Fix issue where missing temp values (nan) are counted as True
    exceed_bool[np.isnan(exceed_bool)] = False

    # Find contiguous regions of exceed_bool = True along time-axis (HeatWaves)
    # Label events for entire domain by flatting the 3D array first
    # in column-major, FORTRAN-style that time-axis stick together. 
    # 1. Perpend zeros at 'zero' time step, that the later flatten 1D columns 
    #    are separated by 'non HeatWave' event
    # 2. Flatten the 3D array in  column-major, FORTRAN-style. This way 
    #    individual time-columns stay together and orders after each other
    # 3. Apply ndimage.label() to find and label connected HeatWave events and 
    #    proceed as 'usual' 
    print(f'start LABLING HW-events')
    tmp_zeros = np.zeros((1,exceed_bool.shape[-2], exceed_bool.shape[-1]))
    tmp_exceed_bool = np.append(tmp_zeros, exceed_bool, axis=0)
    exceed_bool_flatt = tmp_exceed_bool.flatten(order='F')
    events_flatt, n_events = ndimage.label(exceed_bool_flatt)
    # reverse flatting
    events = events_flatt.reshape(tmp_exceed_bool.shape, order='F')
    # remove (out slice) previously added layer of zeros
    events = events[1:,...]
    print(f'events.shape: {events.shape}')
    print(f'Number of events (groups of days) exceeding 90th percentile: {n_events}')
    # store temporal and spartial event information (t,y,x) to disk:
    with nc.Dataset(netCDFFileName, 'a') as nc_file:
        ncVar = nc_file.createVariable('events', 'f8', ('time', 'rlat', 'rlon',),
                                        fill_value=-9999, zlib=True)
        print(f'ncVar.shape: {ncVar.shape}')
        ncVar.standard_name = 'events'
        ncVar.long_name = 'events'
        ncVar.units ='-'
        ncVar.description = 'labeled hot days; 0 indicating "not a hot day"; same label indicating consecutive hot days'
        ncVar.grid_mapping = 'rotated_pole'
        ncVar[...] = events[...]
        ncTime = nc_file.variables['time']
        ncTime[...] = nc_time_hw[...]

    # Labels are unique so np.unique(a, return_counts=True)) could be used
    # to count occurrence of individual events which is equal to event-duration
    # this should be faster than looping over all events...
    print(f'start counting events')
    event_labels, event_label_counts = np.unique(events, return_counts=True)
    # remove (slice out) label 0, as this is 'not a hot day'
    event_labels = event_labels[1:]
    event_label_counts = event_label_counts[1:]
    # What do we have now?
    # -) event_labels 
    #    This variable now holds a unique array of all used labels, which is
    #    basically a range [1,N] where N is the number of events exceeding the 
    #    90p threshold. The length of 'event_labels' does hold the number of 
    #    events exceeding the 90p threshold
    # -) event_label_counts
    #    This variable now holds the number of occurrence of related 
    #    event-label, which is basically the event-duration.
    #    Filtering for different minDurations is therefore easy possible via
    #    >> event_label_counts[event_label_counts>=minDuration]
    #    The length of 'event_label_counts' does also show the number of events
    #    exceeding the 90p threshold. Filtering this array with
    #    >> event_label_counts[event_label_counts>=minDuration]
    #    could show the number of events with duration>=minDuration.
    print(f'Numbers of the heat-events exceeding min. length of {minDuration} days: {event_label_counts[event_label_counts>=minDuration].shape} event')
    ## ADD FOR FINAL USE
    #ALL_event_labels.append(event_labels)
    #ALL_event_label_counts.append(event_label_counts)
    
    ###############################################################################
    #### Calculate some HW properties as intensity etc.
    ###############################################################################
    # First filtering / smooth the noise in daily climatological data
    # --> see also: https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.butter.html
    b, a = signal.butter(3, 0.1, btype='lowpass', analog=False) 
    # using 'axis' argument to control over which axis to smooth the signal. 
    # This way I can apply the function to entire domain at once
    clima_smooth=signal.filtfilt(b, a, clima, axis=0, padlen=15)

    maxEvent     = event_labels[event_label_counts>=minDuration].shape[0]
    currentEvent = 1
    # first try to load already calculated data
    try:
        test = np.load(f'../NIX.npy') # to force 'except'
    except FileNotFoundError:
        ###############################################################################
        #### HEAT WAVE indices calculation
        #### see also: https://github.com/ecjoliver/marineHeatWaves/blob/master/marineHeatWaves.py
        ###############################################################################
        nevents    = event_labels.shape[0]
        mask_value = -9999
        index_start                    = np.full(shape=nevents, fill_value=mask_value, dtype=int)
        index_end                      = np.full(shape=nevents, fill_value=mask_value, dtype=int)
        #date_start                     = np.full(shape=nevents, fill_value=mask_value, dtype=object)
        #date_end                       = np.full(shape=nevents, fill_value=mask_value, dtype=object)
        max_temp                       = np.full(shape=nevents, fill_value=mask_value, dtype=float)
        index_peak                     = np.full(shape=nevents, fill_value=mask_value, dtype=int)
        #date_peak                      = np.full(shape=nevents, fill_value=mask_value, dtype=object)
        duration                       = np.full(shape=nevents, fill_value=mask_value, dtype=float)
        intensity_max                  = np.full(shape=nevents, fill_value=mask_value, dtype=float)
        intensity_mean                 = np.full(shape=nevents, fill_value=mask_value, dtype=float)
        intensity_cumulative           = np.full(shape=nevents, fill_value=mask_value, dtype=float)
        intensity_max_relThresh        = np.full(shape=nevents, fill_value=mask_value, dtype=float)
        intensity_mean_relThresh       = np.full(shape=nevents, fill_value=mask_value, dtype=float)
        intensity_cumulative_relThresh = np.full(shape=nevents, fill_value=mask_value, dtype=float)

        tmp_HWevents = {}
        print(f'needed HWevents file were not found: calculate')
        for ev in event_labels[event_label_counts>=minDuration]:
            print(f'#### Handling event-label {ev} ({currentEvent} of {maxEvent} in {hwYear})') 
            event_idx = np.where(events == ev)
            ##print(f'#### -- event_idx: {event_idx}')
            tt_start       = event_idx[0][0]
            tt_end         = event_idx[0][-1]
            # Pixel of individual HW event
            # assuming (should really be the case) that spatial parts of 
            # 'event_idx' are equal for all time steps of particular event
            pixel_y        = event_idx[-2][0]
            pixel_x        = event_idx[-1][0]

            #tmp_date_start = dailyTime_hw[tt_start]
            #tmp_date_end   = dailyTime_hw[tt_end]
            ##print('tmp_date_start: ', cftime.datetime.strftime(tmp_date_start, '%Y-%m-%d'))
            ##print('tmp_date_end: ', cftime.datetime.strftime(tmp_date_end, '%Y-%m-%d')) 

            index_start[ev] = tt_start
            index_end[ev]   = tt_end
            #date_start[ev]  = tmp_date_start
            #date_end[ev]    = tmp_date_end
            
            temp_mhw = dailyMean_hw[tt_start:tt_end+1,pixel_y,pixel_x]
            thresh_mhw = clima_90p[tt_start:tt_end+1,pixel_y,pixel_x]
            seas_mhw = clima_smooth[tt_start:tt_end+1,pixel_y,pixel_x]        
            
            mhw_relSeas = temp_mhw - seas_mhw
            mhw_relThresh = temp_mhw - thresh_mhw #like in Vautard2013, I will call it 'absolute intensity'
            mhw_relThreshNorm = (temp_mhw - thresh_mhw) / (thresh_mhw - seas_mhw)
                    
            # Find peak
            tt_peak = np.argmax(mhw_relThresh)
            max_temp[ev] = temp_mhw[tt_peak]
            #date_peak[ev] = dailyTime_hw[tt_start+tt_peak]          
            index_peak[ev] = tt_start + tt_peak
            ##print('max_temp: ', temp_mhw[tt_peak])
            ##print('date_peak: ', cftime.datetime.strftime(dailyTime_hw[tt_start+tt_peak], '%Y-%m-%d'))
                            
            # MHW Duration
            duration[ev] = len(mhw_relSeas)
            ##print('duration: ', len(mhw_relSeas), ' days')
            
            # MHW Intensity metrics
            intensity_max[ev] = mhw_relSeas[tt_peak]
            intensity_mean[ev] = mhw_relSeas.mean()
            intensity_cumulative[ev] = mhw_relSeas.sum()
            
            intensity_max_relThresh[ev] = mhw_relThresh[tt_peak]
            intensity_mean_relThresh[ev] = mhw_relThresh.mean()
            ##print('Maximum of abs. intensity:', mhw_relThresh[tt_peak], ' K')
            intensity_cumulative_relThresh[ev] = mhw_relThresh.sum()
            
            print('################ End of the heat event #', ev)
            currentEvent += 1              
    
        with nc.Dataset(netCDFFileName, 'a') as nc_file:
            dev = nc_file.createDimension('evLable', nevents)
            
            ncVar_index_start = nc_file.createVariable('index_start', 'i8', ('evLable',),
                                        fill_value=mask_value, zlib=True)
            ncVar_index_start.standard_name = 'index_start'
            ncVar_index_start.long_name = 'start index in time-axis of event'
            ncVar_index_start.units ='-'
            ncVar_index_start.description = 'start time-step of related event'
            ncVar_index_start[...] = np.ma.masked_where(index_start==mask_value,index_start)[...]

            ncVar_index_end = nc_file.createVariable('index_end', 'i8', ('evLable',),
                                        fill_value=mask_value, zlib=True)
            ncVar_index_end.standard_name = 'index_end'
            ncVar_index_end.long_name = 'end index in time-axis of event'
            ncVar_index_end.units ='-'
            ncVar_index_end.description = 'end time-step of related event'
            ncVar_index_end[...] = np.ma.masked_where(index_end==mask_value,index_end)[...]

            ncVar_max_temp = nc_file.createVariable('max_temp', 'f8', ('evLable',),
                                        fill_value=mask_value, zlib=True)
            ncVar_max_temp.standard_name = 'max_temp'
            ncVar_max_temp.long_name = 'max temperatur of event'
            ncVar_max_temp.units ='K'
            ncVar_max_temp.description = 'max. temperatur of related event'
            ncVar_max_temp[...] = np.ma.masked_where(max_temp==mask_value,max_temp)[...]
            
            ncVar_index_peak = nc_file.createVariable('index_peak', 'i8', ('evLable',),
                                        fill_value=mask_value, zlib=True)
            ncVar_index_peak.standard_name = 'index_peak'
            ncVar_index_peak.long_name = 'index of max_temp in time-axis of event'
            ncVar_index_peak.units ='-'
            ncVar_index_peak.description = 'time-step of max_temp of related event'
            ncVar_index_peak[...] = np.ma.masked_where(index_peak==mask_value,index_peak)[...]

            ncVar_duration = nc_file.createVariable('duration', 'i8', ('evLable',),
                                        fill_value=mask_value, zlib=True)
            ncVar_duration.standard_name = 'duration'
            ncVar_duration.long_name = 'duration of event'
            ncVar_duration.units ='-'
            ncVar_duration.description = 'duration of related event'
            ncVar_duration[...] = np.ma.masked_where(duration==mask_value,duration)[...]

            ncVar_intensity_max = nc_file.createVariable('intensity_max', 'f8', ('evLable',),
                                        fill_value=mask_value, zlib=True)
            ncVar_intensity_max.standard_name = 'intensity_max'
            ncVar_intensity_max.long_name = 'max intensity of event'
            ncVar_intensity_max.units ='K'
            ncVar_intensity_max.description = 'max intensity of related event'
            ncVar_intensity_max[...] = np.ma.masked_where(intensity_max==mask_value,intensity_max)[...]

            ncVar_intensity_mean = nc_file.createVariable('intensity_mean', 'f8', ('evLable',),
                                        fill_value=mask_value, zlib=True)
            ncVar_intensity_mean.standard_name = 'intensity_mean'
            ncVar_intensity_mean.long_name = 'mean intensity of event'
            ncVar_intensity_mean.units ='K'
            ncVar_intensity_mean.description = 'mean intensity of related event'
            ncVar_intensity_mean[...] = np.ma.masked_where(intensity_mean==mask_value,intensity_mean)[...]

            ncVar_intensity_cumulative = nc_file.createVariable('intensity_cumulative', 'f8', ('evLable',),
                                        fill_value=mask_value, zlib=True)
            ncVar_intensity_cumulative.standard_name = 'intensity_cumulative'
            ncVar_intensity_cumulative.long_name = 'cumulative intensity of event'
            ncVar_intensity_cumulative.units ='K'
            ncVar_intensity_cumulative.description = 'cumulative intensity of related event'
            ncVar_intensity_cumulative[...] = np.ma.masked_where(intensity_cumulative==mask_value,intensity_cumulative)[...]

            ncVar_intensity_max_relThresh = nc_file.createVariable('intensity_max_relThresh', 'f8', ('evLable',),
                                        fill_value=mask_value, zlib=True)
            ncVar_intensity_max_relThresh.standard_name = 'intensity_max_relThresh'
            ncVar_intensity_max_relThresh.long_name = 'max relThresh intensity of event'
            ncVar_intensity_max_relThresh.units ='K'
            ncVar_intensity_max_relThresh.description = 'max relThresh intensity of related event'
            ncVar_intensity_max_relThresh[...] = np.ma.masked_where(intensity_max_relThresh==mask_value,intensity_max_relThresh)[...]

            ncVar_intensity_mean_relThresh = nc_file.createVariable('intensity_mean_relThresh', 'f8', ('evLable',),
                                        fill_value=mask_value, zlib=True)
            ncVar_intensity_mean_relThresh.standard_name = 'intensity_mean_relThresh'
            ncVar_intensity_mean_relThresh.long_name = 'mean relThresh intensity of event'
            ncVar_intensity_mean_relThresh.units ='K'
            ncVar_intensity_mean_relThresh.description = 'mean relThresh intensity of related event'
            ncVar_intensity_mean_relThresh[...] = np.ma.masked_where(intensity_mean_relThresh==mask_value,intensity_mean_relThresh)[...]

            ncVar_intensity_cumulative_relThresh = nc_file.createVariable('intensity_cumulative_relThresh', 'f8', ('evLable',),
                                        fill_value=mask_value, zlib=True)
            ncVar_intensity_cumulative_relThresh.standard_name = 'intensity_cumulative_relThresh'
            ncVar_intensity_cumulative_relThresh.long_name = 'cumulative relThresh intensity of event'
            ncVar_intensity_cumulative_relThresh.units ='K'
            ncVar_intensity_cumulative_relThresh.description = 'cumulative relThresh intensity of related event'
            ncVar_intensity_cumulative_relThresh[...] = np.ma.masked_where(intensity_cumulative_relThresh==mask_value,intensity_cumulative_relThresh)[...]


        ##if not os.path.exists(f'../data/example_HWevents/'):
        ##    os.makedirs(f'../data/example_HWevents/')
        ##with open(f'../data/example_HWevents/HWevents_{hwYear}.npy', 'wb') as f:
        ##    np.save(f, tmp_HWevents)
        ### ADD FOR FINAL USE
        ### merging two dicts:
        ##HWevents = {**HWevents, **tmp_HWevents}

"""
# create ndarray out of lst appended to for individual years
ALL_event_labels = np.concatenate(ALL_event_labels, axis=0)
ALL_event_label_counts = np.concatenate(ALL_event_label_counts, axis=0)
# why is below needed?
# should passed somewhat else.
varName = 'TSA'
###############################################################################
#### Plot stuff Fig.5 Vautard
###############################################################################
# set array of individual durations in days
days = np.arange(1,16)
# Find and sum up all events equal to some ref-value, what is equal of finding
# and counting events equal to a given duration.
# (--1--) >> np.sum(ALL_event_label_counts==tmpThreshold)
# Apply this to all values / durations / days and save as ndarray
# (--2--) >> np.array([ (--1--) for tmpThreshold in days] )
HeventsA = np.array([ np.sum(ALL_event_label_counts==tmpThreshold) for tmpThreshold in days] )
# normalize by 'land-pixel'
HeventsA_mean = HeventsA / landPixels
HeventsB = np.array([ np.sum(ALL_event_label_counts>=tmpThreshold) for tmpThreshold in days] )
HeventsB_mean = HeventsB / landPixels

fig, ax = plt.subplots(figsize=(9,5))
ax.grid()
ax.plot(days,HeventsA_mean, color='blue', label='TSMP heatwaves '+ varName + '(= duration from X-axis)', linewidth=2)  #varName
ax.plot(days,HeventsB_mean, color='red', label='TSMP heatwaves '+ varName + '(>=duration from X-axis)', linewidth=2)  #varName
#ax.plot(days, n_event_modified, color='red', label='TSMP heatwaves (>90th percentile of '+ varName +')', linewidth=2)  #varName, L504='>90p'
#ax.set_xticks(labels_idx)
ax.legend(loc='upper center', fancybox=True, shadow=True)
plt.title('TSMP '+str(varName) + ' heat waves investigation',fontsize=12,fontweight='bold')                
plt.xlabel('Duration [days]', fontsize=11)
plt.ylabel('Mean numbers of events', fontsize=11)
plt.savefig(f'Mean_numbers_heatwaves_{varName}.pdf') 
# plt.show()

###############################################################################
#### Plot stuff Fig.6a Vautard, 2013

###############################################################################
# set array of individual amplitudes 
amplitude = np.arange(0,9)
# get labels of all events exceeding a given minDuration. As described above
# the length of this array is equal to the number of events exceeding the 
# minDuration.
maxEvent  = ALL_event_labels[ALL_event_label_counts>=minDuration].shape[0]
frequencyGTamplitude = np.array([ len({k:v for k,v in HWevents.items() if v['duration'] >= minDuration and v['intensity_max_relThresh'] >= tmpThreshold}) for tmpThreshold in amplitude], dtype=float)
frequencyGTamplitude *= 1./maxEvent
fig, ax = plt.subplots(figsize=(9,5))
ax.grid()
ax.plot(amplitude,frequencyGTamplitude, color='red', label='TSMP heatwaves with duration >=6 days & >= amplitude from X-axis', linewidth=2)  #varName
ax.legend(loc='upper right', fancybox=True, shadow=True,  ncol=4)
plt.title('TSMP '+str(varName) + ' heat waves investigation',fontsize=12,fontweight='bold')                
plt.xlabel('Amplitude (T excess of 90th percentile) [K]', fontsize=11)
plt.ylabel('Frequency of events', fontsize=11)
plt.savefig(f'Mean_frequency_amplitude_{varName}.pdf') 
# plt.show()

# ###############################################################################
# #### Plot stuff Fig.7 Vautard, 2013
# ###############################################################################
# fig, ax = plt.subplots(figsize=(9,5))
# ax.grid()
# ax.plot(tot_year,freq_tot_hw_days, color='red', label='TSMP mean hot days (exceeding 90th percentile)', linewidth=2)  #varName
# ax.legend(loc='upper left', fancybox=True, shadow=True)
# plt.title('TSMP '+str(varName) + ' heat waves investigation: JJA '+str(year_start_hw) + '-' +str(year_final_hw),fontsize=12,fontweight='bold')                
# plt.xlabel('Year', fontsize=11)
# plt.ylabel('Hot days frquency', fontsize=11)
# plt.savefig(f'Mean_frequency_hot_days_{varName}.png', dpi=380) 
# plt.show()
# exit()
"""
