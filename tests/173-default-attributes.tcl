package require Tcldot

proc check_default_relayout {case object form attribute value} {
    set c [canvas .c$case -bd 0]
    pack $c
    set g [dotnew digraph]
    set n1 [$g addnode n1]
    $g addnode n2
    set e [$g addedge n1 n2]

    $g layout
    eval [$g render]
    update
    set before [$c bbox all]
    $c delete all

    set setter set${object}attributes
    if {$form eq "list"} {
        $g $setter [list $attribute $value]
    } else {
        $g $setter $attribute $value
    }

    set target [expr {$object eq "node" ? $n1 : $e}]
    set actual [lindex [$target queryattributes $attribute] 0]
    if {$actual ne $value} {
        error "$case mutation was skipped: expected '$value', got '$actual'"
    }

    $g layout
    eval [$g render]
    update
    set after [$c bbox all]
    if {$after eq $before} {
        error "$case did not recompute layout: bbox stayed '$before'"
    }

    $g delete
    destroy $c
}

check_default_relayout node-list node list width 3
check_default_relayout node-pairs node pairs height 2
check_default_relayout edge-list edge list label "a deliberately wide edge label"
check_default_relayout edge-pairs edge pairs label "another deliberately wide edge label"
exit
