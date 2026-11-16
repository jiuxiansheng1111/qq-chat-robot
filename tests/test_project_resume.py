from activity_fixtures import host_at, turn

from app.astrbot_project_resume import ProjectResume


async def test_project_resume_update_list_delete_and_scope(tmp_path):
    tool,host,key = ProjectResume(),host_at(tmp_path),("qq","bot","g","A")
    assert "船体" in await turn(tool,host,key,1,"保存项目 小船：船体做好了，下一步刷漆")
    assert "刷漆" in await turn(ProjectResume(),host,key,2,"继续上次的项目")
    assert "没有" in await turn(tool,host,("qq","bot","g","B"),3,"继续上次的项目")
    assert "装帆" in await turn(tool,host,key,4,"存个进度 小船：现在要装帆")
    assert "小船" in await turn(tool,host,key,5,"我的项目清单")
    assert "删掉" in await turn(tool,host,key,6,"删除项目小船")
    assert "没有" in await turn(tool,host,key,7,"继续上次的项目")
