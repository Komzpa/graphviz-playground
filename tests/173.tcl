package require Tcldot
wm title . "Fluid"
set w .g
set c [canvas $w -bd 0]
pack $c
set g [dotnew digraph rankdir LR]
$g addnode nx
$g layout
eval [$g render]
update
$g addnode n1
$g addnode n2
$g addedge n1 n2 label "e"
$c delete all
update
$g layout
eval [$g render]
