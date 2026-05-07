alias ls="ls --color=auto"
alias ll="ls -lh"
alias la="ls -lha"

alias ..="cd .."
alias ...="cd ../.."
alias ....="cd ../../.."

alias ip="ip -c"

COL_YEL="\[\e[1;33m\]"
COL_GRA="\[\e[0;37m\]"
COL_WHI="\[\e[1;37m\]"
COL_GRE="\[\e[1;32m\]"
COL_RED="\[\e[1;31m\]"
COL_BLU="\[\e[1;34m\]"

if test "$USER" == "root"; then
  _COL_USER=$COL_RED
  _p=" #"
else
  _COL_USER=$COL_GRE
  _p=" $"
fi

COLORIZED_PROMPT="${_COL_USER}\u${COL_WHI}@${COL_GRA}\h${COL_WHI}:\w${_p} \[\e[m\]"

case $TERM in
  linux | *term* | rxvt | screen | vt100 )
    PS1="${COLORIZED_PROMPT}"
    unset COLORIZED_PROMPT ;;
  * )
    PS1="\u@\h:\w${_p} " ;;
esac

unset COL_YEL COL_GRA COL_WHI COL_GRE COL_RED COL_BLU _COL_USER _p

cd "$HOME"
