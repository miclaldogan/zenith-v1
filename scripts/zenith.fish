# fish completions for zenith (installed by scripts/install.sh)
complete -c zenith -f
set -l cmds init db doctor project task scan evidence checkin brief backup director run worker artifacts decisions adapters sandbox demo identity smoke misa
complete -c zenith -n "not __fish_seen_subcommand_from $cmds" -a "$cmds"
complete -c zenith -n "__fish_seen_subcommand_from project" -a "add list show sources discover inspect docs"
complete -c zenith -n "__fish_seen_subcommand_from task" -a "add list transition approve revoke approvals depend undepend"
complete -c zenith -n "__fish_seen_subcommand_from run" -a "create status list cancel reconcile accept"
complete -c zenith -n "__fish_seen_subcommand_from adapters" -a "list probe"
complete -c zenith -n "__fish_seen_subcommand_from misa" -l once -l eval -l live-plan -l live-check -l provider
complete -c zenith -n "__fish_seen_subcommand_from doctor" -l full
complete -c zenith -n "__fish_seen_subcommand_from db" -a "status rehearse migrate"
