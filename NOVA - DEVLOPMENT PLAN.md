This is a plan to overhaul NOVA to make the tool more user friendly, easier to handle, and more simple to handle data.

# Sources Overhaul
Current implementation has the source, database, and test separate. This is only really applicable for data stored in a PostgreSQL. Alternatively, these fields can be combined into a single source field. The required source types will be different menus to accommodate the input. 

The general UI change will be, when the plus is clicked, a new window will appear (no dropdown). That has a list of potential sources to choose from. 

Additional function is the ability to edit sources after being added. 

## RedScale and BlueScale
These are the integrated sources that have a specific setup, being unique tables for test type, and split by test ID. In this case, I want the add button to open a menu with 3 columns consisting of the following. 
- column 1 - PostgreSQL DB (Redscale or Bluescale or other)
- column 2 - table/tests of interest (ptf, hfr, etc)
- column 3 - test id
From this menu, any number of tests can be selected and once applied they will all show up as distinct tests in the sources. Format as TimeScale_DB/TestTable/TestID

# TDMS
once the TDMS option is selected, the file explorer opens and the file can be selected. After selecting the source is directly added to the source list. The source can then be renamed by double clicking or right click to open menu to rename. 

# CSV/h5
This option will be renamed to Data Files. This option will start by opening the file explorer to allow for the data file to be selected, but after selected it will take the user to a menu to input the relevant information. Including source name, source file path, and whether the units are in the headers of the data (option)

# Source and Channel Behavior
This is referring to the behavior on what selected data is plotted in the view. Currently, channels and sources at like buttons that can be enabled or disabled. I want the behavior to be like selecting files in the file explorer, where a single click will select only that one element, shift click will select a range of elements, and ctrl click for multiselect elements. 

I also want to change the behavior for what happens when nothing is selected. If no channels or sources are selected, then assume that all channels and/or sources are to be used. Additionally, there should be space to clear your selection by clicking on the whitespace, similar to file explorer select. 

# Channels from Menu Behavior
Current behavior when selecting channels is like enabling and disabling buttons. I want it to be similar to selecting files in file explorer as described above, but I also want to have a clear added function. So the menu will be 2 columns with 2 buttons between (one left arrow and one right arrow). The left column is the available channels and the right column is the Selected Channels. Channels will be selected in the left side, then moved into the Selected Channels using the right arrow. The left arrow will perform this action in reverse, removing channels from selected channels.

# UI Format Overhaul
1. Remove plotjs buttons from top right of view