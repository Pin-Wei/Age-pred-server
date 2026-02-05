#!/bin/bash

# see: https://hackmd.io/@XVs32/tmux_script_workspace_setup

SESSION="pinwei"
CONDA_PATH="/home/aclexp/mambaforge/bin/activate"
ENV_NAME="server"
ROOT="/media/data2/pinwei/Age_pred_server/server"
SCRIPT_NAME_1="server.py"
SCRIPT_NAME_2="get_integrated_result.py"
SCRIPT_NAME_3="predict.py"
SCRIPT_NAME_4="process_textreading.py"

tmux has-session -t $SESSION 2>/dev/null

if [ $? != 0 ]; then

	tmux new -s $SESSION -d 
	sleep 1
	
	tmux send-keys -t $SESSION:0 "conda activate $ENV_NAME" C-m
	tmux send-keys -t $SESSION:0 "cd $ROOT" C-m
	tmux send-keys -t $SESSION:0 "python $SCRIPT_NAME_1" C-m

	tmux split-window -h -l 75% -t $SESSION:0
	tmux send-keys -t $SESSION:0 "conda activate $ENV_NAME" C-m
	tmux send-keys -t $SESSION:0 "cd $ROOT" C-m
	tmux send-keys -t $SESSION:0 "python $SCRIPT_NAME_2" C-m

	tmux split-window -h -l 66% -t $SESSION:0
	tmux send-keys -t $SESSION:0 "conda activate $ENV_NAME" C-m
	tmux send-keys -t $SESSION:0 "cd $ROOT" C-m
	tmux send-keys -t $SESSION:0 "python $SCRIPT_NAME_3" C-m
	
	tmux split-window -h -l 50% -t $SESSION:0
	tmux send-keys -t $SESSION:0 "conda activate $ENV_NAME" C-m
	tmux send-keys -t $SESSION:0 "cd $ROOT" C-m
	tmux send-keys -t $SESSION:0 "python $SCRIPT_NAME_4" C-m
	
	# tmux select-layout -t $SESSION tiled
fi

if [ -t 0 ]; then
    tmux attach -t $SESSION
fi