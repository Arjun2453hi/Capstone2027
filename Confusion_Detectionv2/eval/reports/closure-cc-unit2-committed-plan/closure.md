# Gap closure on the edited deck

Edited deck `05_agentic_editor\output\cc-unit2-slides.edited.pptx`: 307 visible slides, 19 added (change log: 19), 0 original slides not found unchanged (repaired, hidden or edited).

Targeted topics: 6, 8, 9, 10, 11, 12, 14, 16, 19, 20, 21.

## Gap Closure Rate

**0.727** (8 of 11 targeted gaps judged covered after editing; 0 re-investigations failed and count as not closed).

| Topic | Before | After | Closed |
|---|---|---|---|
| 6 | shallow_coverage (0.850) | shallow_coverage (0.300) | no |
| 8 | fragmented_context (0.850) | covered (0.900) | yes |
| 9 | fragmented_context (0.900) | covered (0.900) | yes |
| 10 | fragmented_context (0.850) | covered (0.800) | yes |
| 11 | shallow_coverage (0.780) | covered (0.717) | yes |
| 12 | shallow_coverage (0.870) | covered (0.900) | yes |
| 14 | fragmented_context (0.900) | shallow_coverage (0.400) | no |
| 16 | shallow_coverage (0.650) | shallow_coverage (0.800) | no |
| 19 | shallow_coverage (0.850) | covered (0.900) | yes |
| 20 | shallow_coverage (0.850) | covered (0.925) | yes |
| 21 | shallow_coverage (0.700) | covered (0.925) | yes |

## Evidence support (no LLM)

34 questions the edits claim to address. Best-slide score improved for 9, got worse for 0; the best-matching slide is a newly added slide for 9. This shows the new text is relevant to the question, not that it answers it.

| Topic | Score before | Score after | Best slide is new | Question |
|---|---|---|---|---|
| 14 | 0.866 | 0.866 | no | What are the main types of namespaces Docker uses? |
| 14 | 0.843 | 0.843 | no | What is a Linux namespace and why does Docker use them? |
| 8 | 0.773 | 0.847 | yes | How does Docker improve application portability? |
| 8 | 0.839 | 0.839 | no | Why do containers have less overhead than VMs? |
| 8 | 0.749 | 0.749 | no | What's the difference between a container and a virtual machine? |
| 8 | 0.770 | 0.770 | no | What is Docker and what problem does it solve? |
| 8 | 0.759 | 0.759 | no | Why are containers considered lightweight virtualization? |
| 8 | 0.776 | 0.815 | yes | What are the main benefits of using Docker? |
| 9 | 0.786 | 0.848 | yes | What do containers share with the host OS? |
| 11 | 0.789 | 0.789 | no | What are the actual commands to create a VETH pair and attach each end to a different namespace? |
| 11 | 0.760 | 0.760 | no | What's the exact procedure for moving one end of a veth pair into a namespace? |
| 11 | 0.799 | 0.799 | no | How do you configure a veth pair step by step to let two namespaces talk to each other? |
| 11 | 0.798 | 0.798 | no | Why is pivot_root often used instead of chroot? |
| 11 | 0.781 | 0.781 | no | What's the difference between chroot and pivot_root? |
| 6 | 0.856 | 0.856 | no | Why was the x86 pushf/popf instruction a problem for virtualization? |
| 16 | 0.712 | 0.734 | yes | If a nested namespace changes its hostname, does the parent namespace ever see that change? |
| 16 | 0.789 | 0.789 | no | What are the actual commands to create a VETH pair and attach each end to a different namespace? |
| 16 | 0.760 | 0.760 | no | What's the exact procedure for moving one end of a veth pair into a namespace? |
| 16 | 0.823 | 0.823 | no | Can you give a concrete example of a cgroup hierarchy and how limits are applied within it? |
| 20 | 0.660 | 0.693 | yes | What does 'eliminate manual bottlenecks' actually mean in practice for a CD pipeline? |
| 20 | 0.763 | 0.763 | no | What's the reasoning behind each of the four continuous delivery principles? |
| 20 | 0.791 | 0.791 | no | Can you explain what makes automated tests 'trustworthy' in continuous delivery? |
| 20 | 0.790 | 0.790 | no | Why exactly does 'every build is a potential release' matter in continuous delivery? |
| 21 | 0.736 | 0.750 | yes | What are the actual differences between Amazon ECS and Google Container Engine? |
| 21 | 0.809 | 0.809 | no | Which container orchestration tool should I pick and why, based on their tradeoffs? |
| 21 | 0.813 | 0.877 | yes | How exactly does Docker Swarm compare to Kubernetes in terms of features and tradeoffs? |
| 12 | 0.823 | 0.823 | no | Can you give a concrete example of a cgroup hierarchy and how limits are applied within it? |
| 12 | 0.831 | 0.831 | no | How are cgroups structured hierarchically? |
| 12 | 0.877 | 0.877 | no | What would an actual cgroup resource-limit example look like in practice? |
| 12 | 0.817 | 0.817 | no | Is there a worked example showing how cgroups restrict a group of processes? |
| 12 | 0.823 | 0.830 | yes | How is a specific CPU or memory limit actually enforced through a cgroup example? |
| 12 | 0.733 | 0.733 | no | Does a child process inherit its parent's cgroup? |
| 10 | 0.871 | 0.871 | no | How do image layers work in Docker? |
| 19 | 0.633 | 0.790 | yes | Why do development and operations teams historically have conflicting goals? |
