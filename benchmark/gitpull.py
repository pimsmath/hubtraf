from nbgitpuller import GitPuller
gp = GitPuller('https://github.com/steven-wolfman/ubc-cpsc103-2019W1-syzygy-distro', 'master', './cs103')
output = []
[output.append(line) for line in gp.pull()]
gp.repo_is_dirty()
