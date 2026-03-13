## Structure

src dir : codes to extract information source. src/pipeline is the complete extraction pipeline.

fork dir: SWE-agent; SWE-bench-Live (compatible to eval old swebench); SWE-bench-Pro

## Environment

### for src dir

just pip install . at the root dir

### for each forked proj

create a separate venv for each proj. 

follow their original installation guides. 

remember that the swerex lib in SWE-agent needs to be replaced by our modified version venv/lib/python3.12/site-packages/swerex.
